import yfinance as yf
import pandas as pd
import numpy as np
import smtplib, os, json, requests
from email.mime.text import MIMEText
from datetime import datetime, time
import pytz

IST = pytz.timezone('Asia/Kolkata')
now_ist = datetime.now(IST)
if not (now_ist.time() >= time(20,0) and now_ist.weekday() < 5): exit()

HISTORY_FILE = 'eod_history.json'
DATE = now_ist.strftime('%m-%d')

# 1. FII/DII Data with futures/options split
try:
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json', 'Referer': 'https://www.nseindia.com'}
    sess = requests.Session()
    sess.get("https://www.nseindia.com", headers=headers, timeout=5)
    nse = sess.get("https://www.nseindia.com/api/fiidiiTradeReact", headers=headers, timeout=10).json()

    fii_cash = float(nse['fii']['buyValue'] - nse['fii']['sellValue']) / 10000000
    dii_cash = float(nse['dii']['buyValue'] - nse['dii']['sellValue']) / 10000000
    fii_index_fut = float(nse['fii'].get('indexFutBuyValue',0) - nse['fii'].get('indexFutSellValue',0)) / 10000000
    fii_index_opt = float(nse['fii'].get('indexOptBuyValue',0) - nse['fii'].get('indexOptSellValue',0)) / 10000000
    fii_stock_fut = float(nse['fii'].get('stockFutBuyValue',0) - nse['fii'].get('stockFutSellValue',0)) / 10000000
    fii_stock_opt = float(nse['fii'].get('stockOptBuyValue',0) - nse['fii'].get('stockOptSellValue',0)) / 10000000

    fii_fut_total = fii_index_fut + fii_stock_fut
    fii_opt_total = fii_index_opt + fii_stock_opt
except:
    fii_cash = dii_cash = fii_fut_total = fii_opt_total = 0

# 2. Sectors - daily top 5 and bottom 5
sectors = {'BANK': '^NSEBANK', 'IT': '^CNXIT', 'AUTO': '^CNXAUTO', 'FMCG': '^CNXFMCG',
           'PHARMA': '^CNXPHARMA', 'METAL': '^CNXMETAL', 'REALTY': '^CNXREALTY', 'ENERGY': '^CNXENERGY'}
sector_perf = {}
for name, sym in sectors.items():
    try:
        h = yf.Ticker(sym).history(period='2d')['Close']
        if len(h) == 2: sector_perf[name] = round((h.iloc[-1]/h.iloc[-2]-1)*100, 1)
    except: continue

top5_sectors = [s[0] for s in sorted(sector_perf.items(), key=lambda x: x[1], reverse=True)[:5]]
bottom5_sectors = [s[0] for s in sorted(sector_perf.items(), key=lambda x: x[1])[:5]]

# 3. Stocks - daily top 5 gainers and losers
nifty50 = ['RELIANCE.NS','TCS.NS','HDFCBANK.NS','ICICIBANK.NS','INFY.NS','ITC.NS','SBIN.NS','LT.NS','AXISBANK.NS','KOTAKBANK.NS',
           'MARUTI.NS','SUNPHARMA.NS','HCLTECH.NS','WIPRO.NS','ONGC.NS','NTPC.NS','TATAMOTORS.NS','BAJFINANCE.NS','ADANIENT.NS','TITAN.NS',
           'ASIANPAINT.NS','NESTLEIND.NS','ULTRACEMCO.NS','POWERGRID.NS','HINDUNILVR.NS']
stock_perf = {}
for sym in nifty50:
    try:
        h = yf.Ticker(sym).history(period='2d')
        if len(h) < 2: continue
        chg = (h['Close'].iloc[-1]/h['Close'].iloc[-2]-1)*100
        stock_perf[sym.replace('.NS','')] = round(chg, 1)
    except: continue

top5_gainers = [s[0] for s in sorted(stock_perf.items(), key=lambda x: x[1], reverse=True)[:5]]
top5_losers = [s[0] for s in sorted(stock_perf.items(), key=lambda x: x[1])[:5]]

# 4. Load & update history - 5 DAYS
if os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE) as f: history = json.load(f)
else:
    history = {'fii': [], 'top_sectors': {}, 'bottom_sectors': {}, 'gainers': {}, 'losers': {}}

# FII history
history['fii'] = [h for h in history['fii'] if h['date']!= DATE]
history['fii'] = [{'date': DATE, 'fii_cash': int(fii_cash), 'fii_fut': int(fii_fut_total),
                   'fii_opt': int(fii_opt_total), 'dii_cash': int(dii_cash)}] + history['fii'][:4]

# Store by date for easy column display
history['top_sectors'][DATE] = top5_sectors
history['bottom_sectors'][DATE] = bottom5_sectors
history['gainers'][DATE] = top5_gainers
history['losers'][DATE] = top5_losers

# Keep only last 5 dates
dates = [h['date'] for h in history['fii']]
history['top_sectors'] = {d: history['top_sectors'][d] for d in dates if d in history['top_sectors']}
history['bottom_sectors'] = {d: history['bottom_sectors'][d] for d in dates if d in history['bottom_sectors']}
history['gainers'] = {d: history['gainers'][d] for d in dates if d in history['gainers']}
history['losers'] = {d: history['losers'][d] for d in dates if d in history['losers']}

with open(HISTORY_FILE, 'w') as f: json.dump(history, f)

# 5. Smart Money Score - 3 day from 5-day history
fii_3d_cash = sum(h['fii_cash'] for h in history['fii'][:3])
fii_3d_fut = sum(h['fii_fut'] for h in history['fii'][:3])
fii_3d_opt = sum(h['fii_opt'] for h in history['fii'][:3])
dii_3d_cash = sum(h['dii_cash'] for h in history['fii'][:3])

smart_money_score = fii_3d_cash + (4 * fii_3d_fut) + (2 * fii_3d_opt) + (0.5 * dii_3d_cash)

bias, impact = "NEUTRAL", "Sideways open."
if smart_money_score > 5000:
    bias, impact = "BULLISH", f"Smart Money Long ₹{smart_money_score:.0f}Cr"
elif smart_money_score < -5000:
    bias, impact = "BEARISH", f"Smart Money Short ₹{abs(smart_money_score):.0f}Cr"
elif fii_3d_fut > 2000:
    bias, impact = "BULLISH", f"FII Futures Long ₹{fii_3d_fut:.0f}Cr"
elif fii_3d_fut < -2000:
    bias, impact = "BEARISH", f"FII Futures Short ₹{abs(fii_3d_fut):.0f}Cr"

# 6. Build email - DATE COLUMNS ON TOP
dates = [h['date'] for h in history['fii']]

body = f"""=== SMART MONEY FLOW ===
FII Cash: {fii_cash:+.0f}Cr | FII Futures: {fii_fut_total:+.0f}Cr | FII Options: {fii_opt_total:+.0f}Cr | DII: {dii_cash:+.0f}Cr
Smart Money Score: {smart_money_score:+.0f}Cr | Verdict: {bias} | {impact}

=== FII FLOW (Cr) ===
{'Date':<12} {' | '.join(dates)}
{'-'*50}
"""
for metric in ['fii_cash', 'fii_fut', 'fii_opt', 'dii_cash']:
    row = f"{metric:<12} "
    row += " | ".join([f"{next((h[metric] for h in history['fii'] if h['date']==d), 0):+6.0f}" for d in dates])
    body += row + "\n"

body += f"\n=== TOP 5 SECTORS BY DAY ===\n"
body += f"{'Date':<7} {' | '.join(dates)}\n{'-'*50}\n"
for i in range(5):
    row = f"Rank {i+1:<2} "
    row += " | ".join([f"{history['top_sectors'].get(d, ['']*5)[i] if i < len(history['top_sectors'].get(d, [])) else '':<6}" for d in dates])
    body += row + "\n"

body += f"\n=== BOTTOM 5 SECTORS BY DAY ===\n"
body += f"{'Date':<7} {' | '.join(dates)}\n{'-'*50}\n"
for i in range(5):
    row = f"Rank {i+1:<2} "
    row += " | ".join([f"{history['bottom_sectors'].get(d, ['']*5)[i] if i < len(history['bottom_sectors'].get(d, [])) else '':<6}" for d in dates])
    body += row + "\n"

body += f"\n=== TOP 5 GAINERS BY DAY ===\n"
body += f"{'Date':<7} {' | '.join(dates)}\n{'-'*50}\n"
for i in range(5):
    row = f"Rank {i+1:<2} "
    row += " | ".join([f"{history['gainers'].get(d, ['']*5)[i] if i < len(history['gainers'].get(d, [])) else '':<10}" for d in dates])
    body += row + "\n"

body += f"\n=== TOP 5 LOSERS BY DAY ===\n"
body += f"{'Date':<7} {' | '.join(dates)}\n{'-'*50}\n"
for i in range(5):
    row = f"Rank {i+1:<2} "
    row += " | ".join([f"{history['losers'].get(d, ['']*5)[i] if i < len(history['losers'].get(d, [])) else '':<10}" for d in dates])
    body += row + "\n"

msg = MIMEText(body)
msg['Subject'] = f"EOD REPORT | SmartMoney {smart_money_score:+.0f}Cr | {bias}"
msg['From'], msg['To'] = os.getenv('GMAIL_USER'), os.getenv('TO_EMAIL')

with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
    s.login(os.getenv('GMAIL_USER'), os.getenv('GMAIL_PASS'))
    s.send_message(msg)
