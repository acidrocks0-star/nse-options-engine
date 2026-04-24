import yfinance as yf
import pandas as pd
import numpy as np
import smtplib
from email.mime.text import MIMEText
import os
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

def calc_max_pain(calls, puts):
    strikes = sorted(set(calls['strike']).union(set(puts['strike'])))
    pain = []
    for strike in strikes:
        call_loss = ((calls['strike'] - strike).clip(lower=0) * calls['openInterest']).sum()
        put_loss = ((strike - puts['strike']).clip(lower=0) * puts['openInterest']).sum()
        pain.append(call_loss + put_loss)
    return strikes[np.argmin(pain)]

def rate_entry(opt, spot, signal_type, pcr):
    """Rate A/B/C based on IV, moneyness, OI, volume"""
    score = 0
    iv = opt['impliedVolatility'] * 100
    dist = abs(opt['strike'] - spot) / spot * 100 # % away from spot

    # IV Score: Lower IV = better for buyers
    if iv < 15: score += 3
    elif iv < 25: score += 2
    else: score += 1

    # Moneyness: ATM/ITM better
    if dist < 1: score += 3 # ATM
    elif dist < 2.5: score += 2 # Near ATM
    else: score += 1

    # Liquidity: OI + Volume
    if opt['openInterest'] > 100000 and opt['volume'] > 50000: score += 3
    elif opt['openInterest'] > 50000: score += 2
    else: score += 1

    # Signal confluence
    if signal_type == 'BREAKOUT' and ((opt['strike'] > spot and pcr < 1) or (opt['strike'] < spot and pcr > 1)): score += 2

    if score >= 9: return 'A'
    elif score >= 6: return 'B'
    else: return 'C'

def get_trade_params(option_row, signal_type, spot, pcr):
    ltp = option_row['lastPrice']
    if ltp < 5: return None

    if signal_type == 'BREAKOUT':
        entry = round(ltp * 1.02, 1)
        sl = round(ltp * 0.80, 1)
        risk = entry - sl
        t1 = round(entry + risk * 1.5, 1)
        t2 = round(entry + risk * 3, 1)
        exit_rating = 'A' if pcr < 0.8 or pcr > 1.2 else 'B'
    elif signal_type == 'REVERSAL':
        entry = round(ltp, 1)
        sl = round(ltp * 0.70, 1)
        risk = entry - sl
        t1 = round(entry + risk * 1, 1)
        t2 = round(entry + risk * 2, 1)
        exit_rating = 'A' if abs(option_row['strike'] - spot) < 200 else 'B'
    else: # UNUSUAL
        entry = round(ltp * 1.01, 1)
        sl = round(ltp * 0.75, 1)
        risk = entry - sl
        t1 = round(entry + risk * 2, 1)
        t2 = round(entry + risk * 4, 1)
        exit_rating = 'A' if option_row['volume'] > option_row['openInterest'] * 3 else 'B'

    entry_rating = rate_entry(option_row, spot, signal_type, pcr)
    return {'Entry': entry, 'SL': sl, 'T1': t1, 'T2': t2, 'LTP': round(ltp,1),
            'EntryRating': entry_rating, 'ExitRating': exit_rating}

try:
    nifty = yf.Ticker("^NSEI")
    spot = nifty.history(period='1d')['Close'].iloc[-1]
    expiry = nifty.options[0]
    chain = nifty.option_chain(expiry)
    calls, puts = chain.calls, chain.puts

    # 1. CORE LEVELS
    max_pain = calc_max_pain(calls, puts)

    # 2. S/R LADDER - Top 3 OI levels
    call_walls = calls.nlargest(3, 'openInterest')[['strike','openInterest']].reset_index(drop=True)
    put_walls = puts.nlargest(3, 'openInterest')[['strike','openInterest']].reset_index(drop=True)
    r1, r2, r3 = call_walls['strike'].tolist() + [0,0,0][:3-len(call_walls)]
    s1, s2, s3 = put_walls['strike'].tolist() + [0,0,0][:3-len(put_walls)]

    # 3. PCR
    atm_range = 500
    atm_calls = calls[abs(calls['strike'] - spot) <= atm_range]
    atm_puts = puts[abs(puts['strike'] - spot) <= atm_range]
    pcr = round(atm_puts['openInterest'].sum() / atm_calls['openInterest'].sum(), 2)

    # 4. UNUSUAL ACTIVITY
    call_ua = calls[(calls['volume'] > calls['openInterest'] * 2) & (calls['lastPrice'] > 10)].nlargest(2, 'volume')
    put_ua = puts[(puts['volume'] > puts['openInterest'] * 2) & (puts['lastPrice'] > 10)].nlargest(2, 'volume')

    # 5. GENERATE TRADES
    trades = []

    # Breakout above R1
    if spot > r1 * 0.999 and spot < r1 * 1.01:
        next_strike = calls[calls['strike'] > r1]['strike'].min()
        if pd.notna(next_strike):
            opt = calls[calls['strike'] == next_strike].iloc[0]
            params = get_trade_params(opt, 'BREAKOUT', spot, pcr)
            if params:
                trades.append({'Setup': 'CE BO R1', 'Strike': int(next_strike), 'Type': 'CALL', **params,
                              'Logic': f'Spot {spot:.0f} > R1 {r1:.0f}'})

    # Breakdown below S1
    if spot < s1 * 1.001 and spot > s1 * 0.99:
        next_strike = puts[puts['strike'] < s1]['strike'].max()
        if pd.notna(next_strike):
            opt = puts[puts['strike'] == next_strike].iloc[0]
            params = get_trade_params(opt, 'BREAKOUT', spot, pcr)
            if params:
                trades.append({'Setup': 'PE BD S1', 'Strike': int(next_strike), 'Type': 'PUT', **params,
                              'Logic': f'Spot {spot:.0f} < S1 {s1:.0f}'})

    # Reversal at R1 - PCR oversold
    if spot >= r1 * 0.995 and pcr < 0.7:
        opt = puts.iloc[(puts['strike']-r1).abs().argsort()[:1]].iloc[0]
        params = get_trade_params(opt, 'REVERSAL', spot, pcr)
        if params:
            trades.append({'Setup': 'PE Rev R1', 'Strike': int(opt['strike']), 'Type': 'PUT', **params,
                          'Logic': f'At R1 {r1:.0f} + PCR {pcr} oversold'})

    # Reversal at S1 - PCR overbought
    if spot <= s1 * 1.005 and pcr > 1.3:
        opt = calls.iloc[(calls['strike']-s1).abs().argsort()[:1]].iloc[0]
        params = get_trade_params(opt, 'REVERSAL', spot, pcr)
        if params:
            trades.append({'Setup': 'CE Rev S1', 'Strike': int(opt['strike']), 'Type': 'CALL', **params,
                          'Logic': f'At S1 {s1:.0f} + PCR {pcr} overbought'})

    # Unusual Activity
    for _, opt in call_ua.iterrows():
        params = get_trade_params(opt, 'UNUSUAL', spot, pcr)
        if params:
            trades.append({'Setup': 'UA Call', 'Strike': int(opt['strike']), 'Type': 'CALL', **params,
                          'Logic': f'Vol {opt["volume"]/1000:.0f}K > 2x OI'})

    for _, opt in put_ua.iterrows():
        params = get_trade_params(opt, 'UNUSUAL', spot, pcr)
        if params:
            trades.append({'Setup': 'UA Put', 'Strike': int(opt['strike']), 'Type': 'PUT', **params,
                          'Logic': f'Vol {opt["volume"]/1000:.0f}K > 2x OI'})

    # BUILD EMAIL
    time_str = datetime.now().strftime('%d %b %H:%M')
    subject = f"NSE: {len(trades)} Trades | MP{max_pain} PCR{pcr}"

    sr_table = pd.DataFrame({
        'Level': ['R3','R2','R1','Spot','S1','S2','S3'],
        'Strike': [r3,r2,r1,spot,s1,s2,s3],
        'Type': ['Res','Res','Res','CMP','Sup','Sup','Sup']
    })
    sr_table = sr_table[sr_table['Strike'] > 0]

    if trades:
        df_trades = pd.DataFrame(trades)
        body = f"""
        <h2>Nifty Options - {expiry} | {time_str}</h2>
        <p><b>Spot:</b> {spot:.2f} | <b>Max Pain:</b> {max_pain} | <b>PCR:</b> {pcr}</p>
        <h3>Support/Resistance Ladder</h3>
        {sr_table.to_html(index=False)}
        <h3>Actionable Trades - Rating A=Best, C=Worst</h3>
        {df_trades.to_html(index=False)}
        <p><b>Entry Rating:</b> Based on IV+Liquidity+Moneyness. <b>Exit Rating:</b> Based on PCR+Gamma.
        <br><b>Rules:</b> A-rated entries only if capital <5L. Book 50% at T1, trail rest to T2.</p>
        """
    else:
        body = f"""
        <h2>No Fresh Trades - {time_str}</h2>
        <p><b>Spot:</b> {spot:.2f} | <b>Max Pain:</b> {max_pain} | <b>PCR:</b> {pcr}</p>
        <h3>Support/Resistance Ladder</h3>
        {sr_table.to_html(index=False)}
        <p>Price between S1 {s1:.0f} and R1 {r1:.0f}. Wait for level test.</p>
        """

    msg = MIMEText(body, 'html')
    msg['Subject'] = subject
    msg['From'] = os.environ.get('GMAIL_USER')
    msg['To'] = os.environ.get('TO_EMAIL')

    server = smtplib.SMTP('smtp.gmail.com', 587)
    server.starttls()
    server.login(os.environ.get('GMAIL_USER'), os.environ.get('GMAIL_PASS'))
    server.send_message(msg)
    server.quit()
    print(f"Sent: {len(trades)} trades")

except Exception as e:
    print(f"Failed: {e}")
