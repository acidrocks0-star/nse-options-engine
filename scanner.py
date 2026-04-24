import yfinance as yf
import pandas as pd
import numpy as np
from scipy.stats import norm
import math, smtplib, os
from email.mime.text import MIMEText
from datetime import datetime, time
import pytz

IST = pytz.timezone('Asia/Kolkata')
now_ist = datetime.now(IST)
if not (time(9,15) <= now_ist.time() <= time(15,30) and now_ist.weekday() < 5):
    exit()

def calc_delta_gamma(S, K, T, r, sigma, option_type):
    if T <= 0 or sigma <= 0: return 0, 0
    d1 = (math.log(S/K) + (r + 0.5*sigma**2)*T) / (sigma*math.sqrt(T))
    delta = norm.cdf(d1) if option_type == 'CE' else -norm.cdf(-d1)
    gamma = norm.pdf(d1) / (S * sigma * math.sqrt(T))
    return round(delta,2), round(gamma,4)

nifty = yf.Ticker("^NSEI")
spot = nifty.history(period="1d", interval="1m")['Close'].iloc[-1]
vix = yf.Ticker("^INDIAVIX").history(period="2d")['Close']
vix_now, vix_open = vix.iloc[-1], vix.iloc[0]
vix_chg = vix_now - vix_open

exp = nifty.options[0]
opt_chain = nifty.option_chain(exp)
calls, puts = opt_chain.calls, opt_chain.puts
calls['Type'], puts['Type'] = 'CE', 'PE'
df = pd.concat([calls, puts])

T = (pd.to_datetime(exp) - pd.to_datetime('today')).days / 365
df[['Delta','Gamma']] = df.apply(lambda x: calc_delta_gamma(spot, x['strike'], T, 0.065, x['impliedVolatility'], x['Type']), axis=1, result_type='expand')

# Market Structure
total_call_oi, total_put_oi = calls['openInterest'].sum(), puts['openInterest'].sum()
pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else 0
call_wall = int(calls.nlargest(1, 'openInterest')['strike'].iloc[0])
put_wall = int(puts.nlargest(1, 'openInterest')['strike'].iloc[0])

# Determine Bias First
bias, reason = "NEUTRAL", "Range bound"
if pcr < 0.85 and spot > call_wall:
    bias, reason = "BULLISH", f"PCR {pcr} + Spot > CallWall {call_wall}. Call writers trapped."
elif pcr > 1.15 and spot < put_wall:
    bias, reason = "BEARISH", f"PCR {pcr} + Spot < PutWall {put_wall}. Put writers trapped."

trades = []
for _, row in df.iterrows():
    if row['volume'] < 200 or row['ask'] == 0: continue
    delta, gamma = row['Delta'], row['Gamma']
    oi_chg_pct = (row['volume'] / row['openInterest'].replace(0,1) * 100)

    signal = ""
    # Only take CE if BULLISH, only PE if BEARISH
    if bias == "BULLISH" and row['Type'] == 'CE':
        if 0.25 <= delta <= 0.55 and gamma > 0.002 and oi_chg_pct > 25:
            signal = "GAMMA BLAST"
        elif row['change'] > 0 and oi_chg_pct > 20:
            signal = "BREAKOUT"

    elif bias == "BEARISH" and row['Type'] == 'PE':
        if -0.55 <= delta <= -0.25 and gamma > 0.002 and oi_chg_pct > 25:
            signal = "GAMMA BLAST"
        elif row['change'] > 0 and oi_chg_pct > 20:
            signal = "BREAKDOWN"

    if signal and 0.3 <= abs(delta) <= 0.6: # Only ATM/near-OTM
        trades.append({
            'Strike': int(row['strike']), 'Type': row['Type'], 'Entry': row['ask'],
            'SL': round(row['ask']*0.65,1), 'T1': round(row['ask']*1.5,1), 'T2': round(row['ask']*2.2,1),
            'Signal': signal
        })

if not trades or bias == "NEUTRAL": exit()

# Email - clean
df_mail = pd.DataFrame(trades)
body = f"""Market Bias: {bias}
Reason: {reason}
VIX: {vix_now:.1f} ({vix_chg:+.1f} since 9:15). CallWall: {call_wall}. PutWall: {put_wall}.

{df_mail.to_string(index=False)}
"""
msg = MIMEText(body)
msg['Subject'] = f"NSE: {len(trades)} Trades | {bias} | {df_mail.iloc[0]['Signal']}"
msg['From'], msg['To'] = os.getenv('GMAIL_USER'), os.getenv('TO_EMAIL')

with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
    s.login(os.getenv('GMAIL_USER'), os.getenv('GMAIL_PASS'))
    s.send_message(msg)
