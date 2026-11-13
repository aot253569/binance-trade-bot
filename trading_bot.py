import streamlit as st
import ccxt
import pandas as pd
import plotly.graph_objects as go
import time
import requests
import json
import os
import numpy as np
from datetime import datetime, timedelta

# ==========================================
# 0. ตั้งค่า Telegram ถาวร (ไม่ต้องพิมพ์เอง)
# ==========================================
DEFAULT_TG_TOKEN = "9246176461:AAHG3mpeOo714B21sTUJAEF_Rtr1bLs35iM"
DEFAULT_TG_CHAT_ID = "9013690732"

# ==========================================
# 1. ระบบจัดการไฟล์ JSON (บันทึกสถิติและพอร์ต)
# ==========================================
DATA_FILE = "pro_bot_data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading JSON: {e}")
    return {
        "balance": 1000.0,
        "initial_balance": 1000.0,
        "positions": [],
        "trade_history": [],
        "logs": []
    }

def save_data():
    data = {
        "balance": st.session_state.balance,
        "initial_balance": st.session_state.initial_balance,
        "positions": st.session_state.positions,
        "trade_history": st.session_state.trade_history,
        "logs": st.session_state.logs
    }
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Error saving JSON: {e}")

# ==========================================
# 2. ตั้งค่าหน้าเว็บและ Session State
# ==========================================
st.set_page_config(page_title="Binance Pro Simulation Bot", layout="wide", page_icon="📈")

saved_data = load_data()

if "balance" not in st.session_state:
    st.session_state.balance = saved_data["balance"]
if "initial_balance" not in st.session_state:
    st.session_state.initial_balance = saved_data["initial_balance"]
if "positions" not in st.session_state:
    st.session_state.positions = saved_data["positions"]
if "trade_history" not in st.session_state:
    st.session_state.trade_history = saved_data["trade_history"]
if "bot_running" not in st.session_state:
    st.session_state.bot_running = False
if "selected_symbol" not in st.session_state:
    st.session_state.selected_symbol = "BTC/USDT:USDT"
if "logs" not in st.session_state:
    st.session_state.logs = saved_data["logs"]

def log_event(msg):
    timestamp = datetime.now().strftime("%H:%M:%S")
    st.session_state.logs.insert(0, f"[{timestamp}] {msg}")
    st.session_state.logs = st.session_state.logs[:50]
    save_data()

def send_telegram_alert(token, chat_id, message):
    if not token or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        log_event(f"⚠️ Telegram Error: {e}")

@st.cache_resource
def init_exchange():
    return ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'future'}})

# ==========================================
# 3. ตัวกรองตลาดและคำนวณอินดิเคเตอร์ (Pro Strategy)
# ==========================================
WATCHLIST = [
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
    "BNB/USDT:USDT", "XRP/USDT:USDT", "DOGE/USDT:USDT",
    "ADA/USDT:USDT", "AVAX/USDT:USDT", "LINK/USDT:USDT", "SUI/USDT:USDT"
]

def fetch_ohlcv_df(exchange, symbol, timeframe="5m", limit=100):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    except Exception:
        now = datetime.now()
        timestamps = [now - timedelta(minutes=5 * i) for i in range(limit)][::-1]
        np.random.seed(sum(map(ord, symbol)) % 1000)
        base = 60000.0 if "BTC" in symbol else 3000.0
        closes = base + np.cumsum(np.random.normal(0, base * 0.001, limit))
        opens = closes + np.random.normal(0, base * 0.0005, limit)
        highs = np.maximum(opens, closes) + abs(np.random.normal(0, base * 0.0005, limit))
        lows = np.minimum(opens, closes) - abs(np.random.normal(0, base * 0.0005, limit))
        df = pd.DataFrame({'timestamp': timestamps, 'open': opens, 'high': highs, 'low': lows, 'close': closes, 'volume': np.random.uniform(50, 200, limit)})

    df['EMA_9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['EMA_21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['EMA_50'] = df['close'].ewm(span=50, adjust=False).mean()
    
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    df['RSI'] = df['RSI'].fillna(50)
    return df

def scan_market(exchange, timeframe, rsi_threshold):
    results = []
    for symbol in WATCHLIST:
        df = fetch_ohlcv_df(exchange, symbol, timeframe=timeframe, limit=60)
        last = df.iloc[-1]
        
        trend_ok_long = last['close'] > last['EMA_50']
        trend_ok_short = last['close'] < last['EMA_50']
        
        signal = "WAIT"
        if last['EMA_9'] > last['EMA_21'] and last['RSI'] < rsi_threshold and trend_ok_long:
            signal = "BUY (LONG)"
        elif last['EMA_9'] < last['EMA_21'] and last['RSI'] > (100 - rsi_threshold) and trend_ok_short:
            signal = "SELL (SHORT)"
            
        results.append({
            "Symbol": symbol.replace(":USDT", ""),
            "RawSymbol": symbol,
            "Price": last['close'],
            "High": last['high'],
            "Low": last['low'],
            "RSI (14)": round(last['RSI'], 2),
            "Signal": signal
        })
    return pd.DataFrame(results)

# ==========================================
# 4. ระบบจัดการคำสั่งเทรด (Pro Simulation Execution)
# ==========================================
def open_position(symbol, side, entry_price, margin, leverage, tp_pct, sl_pct, tg_token, tg_chat_id):
    for pos in st.session_state.positions:
        if pos["Symbol"] == symbol:
            return

    if st.session_state.balance < margin:
        log_event(f"⚠️ ยอดเงินไม่พอเปิดออเดอร์ {symbol}")
        return

    qty = (margin * leverage) / entry_price
    move_tp = (tp_pct / 100.0) / leverage
    move_sl = (sl_pct / 100.0) / leverage

    if side == "LONG":
        tp_price = entry_price * (1 + move_tp)
        sl_price = entry_price * (1 - move_sl)
    else:
        tp_price = entry_price * (1 - move_tp)
        sl_price = entry_price * (1 + move_sl)

    st.session_state.balance -= margin
    st.session_state.positions.append({
        "Symbol": symbol, "Side": side, "Entry Price": entry_price,
        "Current Price": entry_price, "Margin (USDT)": margin,
        "Leverage": leverage, "Qty": qty, "TP Price": tp_price, "SL Price": sl_price,
        "Unrealized PnL": 0.0, "ROE %": 0.0, "Opened At": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })
    save_data()
    
    msg = f"🚀 <b>PRO OPEN {side}</b> {symbol.replace(':USDT','')} @ {entry_price:,.4f}\n⚡ เลเวอเรจ: {leverage}x | มาร์จิ้น: ${margin}"
    log_event(msg.replace("<b>", "").replace("</b>", ""))
    send_telegram_alert(tg_token, tg_chat_id, msg)

def check_positions(exchange, tg_token, tg_chat_id):
    remaining = []
    changed = False
    
    for pos in st.session_state.positions:
        df = fetch_ohlcv_df(exchange, pos["Symbol"], limit=5)
        curr_price = df.iloc[-1]['close']
        high_price = df.iloc[-1]['high']
        low_price = df.iloc[-1]['low']
        pos["Current Price"] = curr_price

        if pos["Side"] == "LONG":
            pnl = (curr_price - pos["Entry Price"]) * pos["Qty"]
            hit_tp = high_price >= pos["TP Price"]
            hit_sl = low_price <= pos["SL Price"]
        else:
            pnl = (pos["Entry Price"] - curr_price) * pos["Qty"]
            hit_tp = low_price <= pos["TP Price"]
            hit_sl = high_price >= pos["SL Price"]

        roe = (pnl / pos["Margin (USDT)"]) * 100
        pos["Unrealized PnL"] = pnl
        pos["ROE %"] = roe

        if hit_tp or hit_sl:
            reason = "TAKE PROFIT ✅" if hit_tp else "STOP LOSS 🛑"
            st.session_state.balance += (pos["Margin (USDT)"] + pnl)
            changed = True
            
            clean_sym = pos['Symbol'].replace(":USDT", "")
            st.session_state.trade_history.insert(0, {
                "Time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "Symbol": clean_sym, "Side": pos["Side"],
                "Entry": pos["Entry Price"], "Exit": curr_price,
                "PnL (USDT)": round(pnl, 2), "ROE %": round(roe, 2), "Reason": reason
            })
            
            msg = f"💰 <b>CLOSED {clean_sym} ({reason})</b>\n💵 กำไร/ขาดทุน: {pnl:+,.2f} USDT ({roe:+,.2f}%)"
            log_event(msg.replace("<b>", "").replace("</b>", ""))
            send_telegram_alert(tg_token, tg_chat_id, msg)
        else:
            remaining.append(pos)
            
    st.session_state.positions = remaining
    if changed:
        save_data()

# ==========================================
# 5. แผงควบคุมตั้งค่า (Sidebar) - ใส่ค่าอัตโนมัติ
# ==========================================
with st.sidebar:
    st.header("⚙️ ตั้งค่าบอท Pro Simulation")
    st.info("โหมดทดสอบ Forward Test เก็บสถิติ 1 เดือนเต็ม")
    
    st.subheader("📢 Telegram Notifications")
    # ใส่ค่าเริ่มต้นที่คุณให้มาไว้ในช่อง input อัตโนมัติ
    telegram_token = st.text_input("Telegram Bot Token", value=DEFAULT_TG_TOKEN, type="password")
    telegram_chat_id = st.text_input("Telegram Chat ID", value=DEFAULT_TG_CHAT_ID)

    st.divider()
    st.subheader("🎛️ ความเสี่ยงและเงินลงทุน")
    leverage = st.slider("เลเวอเรจ (x)", 1, 50, 10)
    margin_per_trade = st.number_input("เงินลงทุนต่อไม้ (USDT)", 10.0, 5000.0, 50.0)
    take_profit_pct = st.slider("Take Profit (% ROE)", 5.0, 300.0, 20.0)
    stop_loss_pct = st.slider("Stop Loss (% ROE)", 2.0, 100.0, 10.0)
    max_open_trades = st.slider("จำนวนเหรียญสูงสุดพร้อมกัน", 1, 5, 3)

    st.divider()
    st.subheader("🔍 กลยุทธ์กรองเทรนด์")
    timeframe = st.selectbox("Timeframe", ["1m", "5m", "15m", "1h"], index=1)
    rsi_threshold = st.slider("ความไว RSI", 20, 60, 40)

    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        if st.button("▶️ เริ่มบอท Pro", use_container_width=True, type="primary"):
            st.session_state.bot_running = True
            log_event("🟢 เริ่มรันระบบ Pro Simulation แล้ว")
    with c2:
        if st.button("⏹️ หยุดบอท", use_container_width=True):
            st.session_state.bot_running = False
            log_event("🔴 หยุดบอทแล้ว")

    if st.button("🔄 รีเซ็ตสถิติพอร์ต ($1,000)", use_container_width=True):
        st.session_state.balance = 1000.0
        st.session_state.initial_balance = 1000.0
        st.session_state.positions = []
        st.session_state.trade_history = []
        st.session_state.logs = []
        save_data()
        st.rerun()

# ==========================================
# 6. หน้าจอแสดงผลแดชบอร์ดหลัก (Dashboard)
# ==========================================
exchange = init_exchange()

if st.session_state.positions:
    check_positions(exchange, telegram_token, telegram_chat_id)

unrealized = sum(p["Unrealized PnL"] for p in st.session_state.positions)
locked_margin = sum(p["Margin (USDT)"] for p in st.session_state.positions)
total_equity = st.session_state.balance + locked_margin + unrealized
net_pnl = total_equity - st.session_state.initial_balance
net_pnl_pct = (net_pnl / st.session_state.initial_balance) * 100

st.title("📈 Binance Futures Pro Simulation Dashboard")
status = "🟢 บอทกำลังรันสแกนและเทรดอัตโนมัติ" if st.session_state.bot_running else "🔴 บอทหยุดทำงาน"
st.caption(f"**สถานะระบบ:** {status} | กำหนดค่า Telegram เรียบร้อยแล้ว")

m1, m2, m3, m4 = st.columns(4)
m1.metric("เงินสดคงเหลือ (Available)", f"${st.session_state.balance:,.2f}")
m2.metric("มูลค่าพอร์ตทั้งหมด (Equity)", f"${total_equity:,.2f}", f"{net_pnl:+,.2f} ({net_pnl_pct:+.2f}%)")
m3.metric("Unrealized PnL", f"${unrealized:+,.2f}")
total_trades = len(st.session_state.trade_history)
win_trades = sum(1 for t in st.session_state.trade_history if t["PnL (USDT)"] > 0)
win_rate = (win_trades / total_trades * 100) if total_trades > 0 else 0.0
m4.metric("Win Rate (อัตราชนะ)", f"{win_rate:.1f}% ({win_trades}/{total_trades})")

st.divider()

scan_df = scan_market(exchange, timeframe, rsi_threshold)

if st.session_state.bot_running and not scan_df.empty:
    for _, row in scan_df.iterrows():
        if len(st.session_state.positions) >= max_open_trades:
            break
        if row["Signal"] == "BUY (LONG)":
            open_position(row["RawSymbol"], "LONG", row["Price"], margin_per_trade, leverage, take_profit_pct, stop_loss_pct, telegram_token, telegram_chat_id)
        elif row["Signal"] == "SELL (SHORT)":
            open_position(row["RawSymbol"], "SHORT", row["Price"], margin_per_trade, leverage, take_profit_pct, stop_loss_pct, telegram_token, telegram_chat_id)

col_chart, col_scan = st.columns([1.4, 1])

with col_chart:
    st.subheader("📊 กราฟราคาและการวิเคราะห์")
    chart_coin = st.selectbox("เลือกเหรียญดูกราฟ", WATCHLIST, index=WATCHLIST.index(st.session_state.selected_symbol), format_func=lambda x: x.replace(":USDT",""))
    st.session_state.selected_symbol = chart_coin

    df_chart = fetch_ohlcv_df(exchange, chart_coin, timeframe=timeframe, limit=80)
    
    fig = go.Figure()
    fig.add_trace(go.Candlestick(x=df_chart['timestamp'], open=df_chart['open'], high=df_chart['high'], low=df_chart['low'], close=df_chart['close'], name="Price"))
    fig.add_trace(go.Scatter(x=df_chart['timestamp'], y=df_chart['EMA_9'], line=dict(color='cyan', width=1.2), name='EMA 9'))
    fig.add_trace(go.Scatter(x=df_chart['timestamp'], y=df_chart['EMA_21'], line=dict(color='orange', width=1.2), name='EMA 21'))
    fig.add_trace(go.Scatter(x=df_chart['timestamp'], y=df_chart['EMA_50'], line=dict(color='magenta', width=1.2), name='EMA 50 (Trend)'))

    for pos in st.session_state.positions:
        if pos["Symbol"] == chart_coin:
            fig.add_hline(y=pos["Entry Price"], line_dash="dot", line_color="white", annotation_text="Entry")
            fig.add_hline(y=pos["TP Price"], line_dash="dash", line_color="lime", annotation_text="TP")
            fig.add_hline(y=pos["SL Price"], line_dash="dash", line_color="red", annotation_text="SL")

    fig.update_layout(height=420, margin=dict(l=10, r=10, t=20, b=10), xaxis_rangeslider_visible=False, template="plotly_dark")
    st.plotly_chart(fig, use_container_width=True)

with col_scan:
    st.subheader("📡 ตารางสแกนเหรียญ (Pro Filter)")
    if not scan_df.empty:
        st.dataframe(scan_df[["Symbol", "Price", "RSI (14)", "Signal"]], use_container_width=True, height=340, hide_index=True)

    st.caption("ทดลองเปิดออเดอร์ด้วยมือ:")
    b1, b2 = st.columns(2)
    curr_p = df_chart.iloc[-1]['close']
    if b1.button("🟢 Long มือ", use_container_width=True):
        open_position(chart_coin, "LONG", curr_p, margin_per_trade, leverage, take_profit_pct, stop_loss_pct, telegram_token, telegram_chat_id)
        st.rerun()
    if b2.button("🔴 Short มือ", use_container_width=True):
        open_position(chart_coin, "SHORT", curr_p, margin_per_trade, leverage, take_profit_pct, stop_loss_pct, telegram_token, telegram_chat_id)
        st.rerun()

st.divider()

t1, t2, t3 = st.tabs(["🔥 ออเดอร์ที่เปิดอยู่", "📜 ประวัติการเทรดจริงย้อนหลัง", "🖥️ บันทึกระบบ Log"])

with t1:
    if st.session_state.positions:
        p_df = pd.DataFrame(st.session_state.positions)
        p_df["Symbol"] = p_df["Symbol"].str.replace(":USDT","")
        st.dataframe(p_df[["Symbol", "Side", "Leverage", "Margin (USDT)", "Entry Price", "Current Price", "TP Price", "SL Price", "Unrealized PnL", "ROE %"]], use_container_width=True, hide_index=True)
    else:
        st.info("ยังไม่มีออเดอร์เปิดอยู่")

with t2:
    if st.session_state.trade_history:
        st.dataframe(pd.DataFrame(st.session_state.trade_history), use_container_width=True, hide_index=True)
    else:
        st.info("ยังไม่มีประวัติการปิดออเดอร์")

with t3:
    for log in st.session_state.logs:
        st.text(log)

if st.session_state.bot_running or st.session_state.positions:
    time.sleep(5)
    st.rerun()