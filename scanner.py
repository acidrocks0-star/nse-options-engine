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

def calc_max_pain(calls, puts):
    strikes = sorted(set(calls['strike']).intersection(set(puts['strike'])))
    if not strikes: return 0
    losses = []
    for k in strikes:
        call_loss = sum((k - calls['strike']).clip(lower=0) * calls['openInterest'])
        put_loss = sum((puts['strike'] - k).clip(lower=0) * puts['openInterest'])
        losses.append(call_loss + put_loss)
    return int(strikes[np.argmin(losses)])

def scan_index(symbol, name):
    try:
        idx = yf.Ticker(symbol)
        spot = idx.history(period="1d", interval="1m")['Close'].iloc[-1]
        exp = idx.options[0]
        opt_chain = idx.option_chain(exp)
        calls, puts = opt_chain.calls, opt_chain.puts
        calls['Type'], puts['Type'] = 'CE', 'PE'
        df = pd.concat([calls, puts])

        T = (pd.to_datetime(exp) - pd.to_datetime('today')).days / 365
        df[['Delta','Gamma']] = df.apply(lambda x: calc_delta_gamma(spot, x['strike'], T, 0.065, x['impliedVolatility'], x['Type']), axis=1, result_type='expand')

        max_pain = calc_max_pain(calls, puts)
        total_call_oi, total_put_oi = calls['openInterest'].sum(), puts['openInterest'].sum()
        pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else 0
        call_wall = int(calls.nlargest(1, 'openInterest')['strike'].iloc[0])
        put_wall = int(puts.nlargest(1, 'openInterest')['strike'].iloc[0])

        bias, reason = "NEUTRAL", "Range bound"
        if spot > max_pain and spot > call_wall and pcr < 0.9:
            bias, reason = "BULLISH", f"Spot {spot:.0f} > MaxPain {max_pain} + CallWall {call_wall}"
        elif spot < max_pain and spot < put_wall and pcr > 1.1:
            bias, reason = "BEARISH", f"Spot {spot:.0f} < MaxPain {max_pain} + PutWall {put_wall}"

        trades = []
        for _, row in df.iterrows():
            if row['volume'] < 100 or row['ask'] == 0: continue
            delta, gamma = row['Delta'], row['Gamma']
            oi_chg_pct = (row['volume'] / row['openInterest'].replace(0,1) * 100)

            signal = ""
            if bias == "BULLISH" and row['Type'] == 'CE':
                if 0.25 <= delta <= 0.55 and gamma > 0.002 and oi_chg_pct > 25:
                    signal = "GAMMA BLAST"
            elif bias == "BEARISH" and row['Type'] == 'PE':
                if -0.55 <= delta <= -0.25 and gamma > 0.002 and oi_chg_pct > 25:
                    signal = "GAMMA BLAST"

            if signal and 0.3 <= abs(delta) <= 0.6:
                trades.append({
                    'Strike': int(row['strike']), 'Type': row['Type'], 'Entry': row['ask'],
                    'SL': round(row['ask']*0.65,1), 'T1': round(row['ask']*1.5,1), 'T2': round(row['ask']*2.2,1),
                    'Signal': signal
                })

        return {'name': name, 'spot': spot, 'bias': bias, 'reason': reason,
                'max_pain': max_pain, 'call_wall': call_wall, 'put_wall': put_wall, 'trades': trades}
    except:
        return {'name': name, 'bias': 'ERROR', 'trades': []}

# Scan all indices
vix = yf.Ticker("^INDIAVIX").history(period="2d")['Close']
vix_now, vix_open = vix.iloc[-1], vix.iloc[0]
vix_chg = vix_now - vix_open

indices = [("^NSEI", "NIFTY"), ("^NSEBANK", "BANKNIFTY"), ("^BSESN", "SENSEX")]
results = [scan_index(sym, name) for sym, name in indices]

# Build single email
active_trades = sum(len(r['trades']) for r in results if r['bias']!= 'NEUTRAL')
if active_trades == 0: exit() # No email if all neutral

subject_bias = " ".join([f"{r['name']}:{r['bias']}" for r in results if r['bias']!= 'ERROR'])
body = f"VIX: {vix_now:.1f} ({vix_chg:+.1f} since 9:15)\n\n"

for r in results:
    body += f"=== {r['name']} === {r['bias']}\n"
    if r['bias'] == 'NEUTRAL' or r['bias'] == 'ERROR':
        body += "No trades. Range bound.\n\n"
        continue
    body += f"Reason: {r['reason']}\n"
    body += f"MaxPain: {r['max_pain']} | CallWall: {r['call_wall']} | PutWall: {r['put_wall']}\n"
    body += pd.DataFrame(r['trades']).to_string(index=False) + "\n\n"

msg = MIMEText(body)
msg['Subject'] = f"MARKET SCAN | {subject_bias}"
msg['From'], msg['To'] = os.getenv('GMAIL_USER'), os.getenv('TO_EMAIL')

with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
    s.login(os.getenv('GMAIL_USER'), os.getenv('GMAIL_PASS'))
    s.send_message(msg)
