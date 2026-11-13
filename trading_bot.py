import streamlit as st
import ccxt
import pandas as pd
import plotly.graph_objects as go
import time
import requests
import json
import os
from datetime import datetime

# ==========================================
# 1. ระบบจัดการไฟล์ JSON (โหลดและบันทึกข้อมูล)
# ==========================================
DATA_FILE = "bot_data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading JSON: {e}")
    # ค่าเริ่มต้นถ้ายังไม่มีไฟล์
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
# 2. ตั้งค่าหน้าเว็บ Streamlit และ Session State
# ==========================================
st.set_page_config(page_title="Binance Futures Auto-Bot Pro", layout="wide", page_icon="🤖")

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
    save_data()  # บันทึกลงไฟล์ทุกครั้งที่มี Log ใหม่

# ==========================================
# 3. ฟังก์ชันส่งแจ้งเตือน Telegram
# ==========================================
def send_telegram_alert(token, chat_id, message):
    if not token or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        log_event(f"⚠️ Telegram Error: {e}")

# ==========================================
# 4. เชื่อมต่อ Binance Exchange
# ==========================================
@st.cache_resource
def init_exchange(mode, api_key="", api_secret=""):
    config = {
        'enableRateLimit': True,
        'options': {'defaultType': 'future'}
    }
    if mode != "Paper Trading (Simulation)" and api_key and api_secret:
        config['apiKey'] = api_key
        config['secret'] = api_secret

    exchange = ccxt.binance(config)
    if mode == "Binance Futures Testnet":
        exchange.set_sandbox_mode(True)
    return exchange

# ==========================================
# 5. ตัวสแกนตลาดและกลยุทธ์ (Market Scanner)
# ==========================================
WATCHLIST = [
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
    "BNB/USDT:USDT", "XRP/USDT:USDT", "DOGE/USDT:USDT",
    "ADA/USDT:USDT", "AVAX/USDT:USDT", "LINK/USDT:USDT", "SUI/USDT:USDT"
]

def fetch_ohlcv_df(exchange, symbol, timeframe="5m", limit=100):
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df['EMA_9'] = ta.ema(df['close'], length=9)
    df['EMA_21'] = ta.ema(df['close'], length=21)
    df['RSI'] = ta.rsi(df['close'], length=14)
    return df

def scan_market(exchange, timeframe, rsi_buy_threshold):
    scan_results = []
    for symbol in WATCHLIST:
        try:
            df = fetch_ohlcv_df(exchange, symbol, timeframe=timeframe, limit=50)
            last = df.iloc[-1]
            
            price = last['close']
            rsi = last['RSI']
            ema9 = last['EMA_9']
            ema21 = last['EMA_21']
            
            signal = "WAIT"
            if ema9 > ema21 and rsi < rsi_buy_threshold:
                signal = "BUY (LONG)"
            elif ema9 < ema21 and rsi > (100 - rsi_buy_threshold):
                signal = "SELL (SHORT)"
                
            scan_results.append({
                "Symbol": symbol.replace(":USDT", ""),
                "RawSymbol": symbol,
                "Price": price,
                "RSI (14)": round(rsi, 2),
                "EMA 9": round(ema9, 4),
                "EMA 21": round(ema21, 4),
                "Signal": signal
            })
        except Exception as e:
            continue
    return pd.DataFrame(scan_results)

# ==========================================
# 6. ระบบจำลองการเทรด (Paper Trading & TP/SL)
# ==========================================
def open_paper_position(symbol, side, entry_price, margin_usdt, leverage, tp_pct, sl_pct, tg_token, tg_chat_id):
    for pos in st.session_state.positions:
        if pos["Symbol"] == symbol:
            return

    if st.session_state.balance < margin_usdt:
        log_event(f"⚠️ ยอดเงินไม่พอเปิดออเดอร์ {symbol}")
        return

    position_size_usdt = margin_usdt * leverage
    qty = position_size_usdt / entry_price

    price_move_tp = (tp_pct / 100.0) / leverage
    price_move_sl = (sl_pct / 100.0) / leverage

    if side == "LONG":
        tp_price = entry_price * (1 + price_move_tp)
        sl_price = entry_price * (1 - price_move_sl)
    else:
        tp_price = entry_price * (1 - price_move_tp)
        sl_price = entry_price * (1 + price_move_sl)

    st.session_state.balance -= margin_usdt
    st.session_state.positions.append({
        "Symbol": symbol,
        "Side": side,
        "Entry Price": entry_price,
        "Current Price": entry_price,
        "Margin (USDT)": margin_usdt,
        "Leverage": leverage,
        "Qty": qty,
        "TP Price": tp_price,
        "SL Price": sl_price,
        "Unrealized PnL": 0.0,
        "ROE %": 0.0,
        "Opened At": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })
    
    save_data() # บันทึกลงไฟล์ทันทีหลังเปิดออเดอร์
    
    log_msg = f"🚀 <b>OPEN {side}</b> {symbol.replace(':USDT','')} @ {entry_price:.4f}\n⚡ เลเวอเรจ: {leverage}x | มาร์จิ้น: ${margin_usdt}"
    log_event(log_msg.replace("<b>", "").replace("</b>", ""))
    send_telegram_alert(tg_token, tg_chat_id, log_msg)

def update_and_check_positions(exchange, tg_token, tg_chat_id):
    remaining_positions = []
    has_changes = False
    
    for pos in st.session_state.positions:
        try:
            ticker = exchange.fetch_ticker(pos["Symbol"])
            curr_price = ticker['last']
            pos["Current Price"] = curr_price

            if pos["Side"] == "LONG":
                pnl = (curr_price - pos["Entry Price"]) * pos["Qty"]
                hit_tp = curr_price >= pos["TP Price"]
                hit_sl = curr_price <= pos["SL Price"]
            else:
                pnl = (pos["Entry Price"] - curr_price) * pos["Qty"]
                hit_tp = curr_price <= pos["TP Price"]
                hit_sl = curr_price >= pos["SL Price"]

            roe = (pnl / pos["Margin (USDT)"]) * 100
            pos["Unrealized PnL"] = pnl
            pos["ROE %"] = roe

            if hit_tp or hit_sl:
                reason = "TAKE PROFIT ✅" if hit_tp else "STOP LOSS 🛑"
                st.session_state.balance += (pos["Margin (USDT)"] + pnl)
                has_changes = True
                
                clean_sym = pos['Symbol'].replace(":USDT", "")
                st.session_state.trade_history.insert(0, {
                    "Time": datetime.now().strftime("%H:%M:%S"),
                    "Symbol": clean_sym,
                    "Side": pos["Side"],
                    "Entry": pos["Entry Price"],
                    "Exit": curr_price,
                    "PnL (USDT)": round(pnl, 2),
                    "ROE %": round(roe, 2),
                    "Reason": reason
                })
                
                log_msg = f"💰 <b>CLOSED {clean_sym} ({reason})</b>\n💵 กำไร/ขาดทุน: {pnl:+.2f} USDT ({roe:+.2f}%)"
                log_event(log_msg.replace("<b>", "").replace("</b>", ""))
                send_telegram_alert(tg_token, tg_chat_id, log_msg)
            else:
                remaining_positions.append(pos)
        except Exception as e:
            remaining_positions.append(pos)
            
    st.session_state.positions = remaining_positions
    if has_changes:
        save_data()

def close_all_positions(tg_token, tg_chat_id):
    for pos in st.session_state.positions:
        pnl = pos["Unrealized PnL"]
        st.session_state.balance += (pos["Margin (USDT)"] + pnl)
        clean_sym = pos['Symbol'].replace(":USDT", "")
        st.session_state.trade_history.insert(0, {
            "Time": datetime.now().strftime("%H:%M:%S"),
            "Symbol": clean_sym,
            "Side": pos["Side"],
            "Entry": pos["Entry Price"],
            "Exit": pos["Current Price"],
            "PnL (USDT)": round(pnl, 2),
            "ROE %": round(pos["ROE %"], 2),
            "Reason": "MANUAL CLOSE 🖐️"
        })
    st.session_state.positions = []
    save_data()
    log_event("🖐️ ปิดสถานะทั้งหมดด้วยตนเอง")
    send_telegram_alert(tg_token, tg_chat_id, "🖐️ บอททำการปิดสถานะทั้งหมดด้วยตนเองแล้ว")

# ==========================================
# 7. แผงควบคุมด้านข้าง (Sidebar UI)
# ==========================================
with st.sidebar:
    st.header("⚙️ ตั้งค่าบอทเทรด")
    
    trading_mode = st.selectbox(
        "โหมดการเทรด",
        ["Paper Trading (Simulation)", "Binance Futures Testnet", "Live Trading (Real Money)"]
    )
    
    api_key = ""
    api_secret = ""
    if trading_mode != "Paper Trading (Simulation)":
        api_key = st.text_input("Binance API Key", type="password")
        api_secret = st.text_input("Binance Secret Key", type="password")

    st.divider()
    st.subheader("📢 Telegram Notifications")
    telegram_token = st.text_input("Telegram Bot Token", type="password", placeholder="123456:ABC-DEF...")
    telegram_chat_id = st.text_input("Telegram Chat ID", placeholder="987654321")

    st.divider()
    st.subheader("🎛️ ความเสี่ยงและเลเวอเรจ")
    
    leverage = st.slider("เลเวอเรจ (x)", min_value=1, max_value=50, value=10, step=1)
    margin_per_trade = st.number_input("เงินลงทุนต่อไม้ (USDT)", min_value=10.0, max_value=10000.0, value=100.0, step=10.0)
    take_profit_pct = st.slider("Take Profit (% ROE)", min_value=1.0, max_value=200.0, value=15.0, step=1.0)
    stop_loss_pct = st.slider("Stop Loss (% ROE)", min_value=1.0, max_value=100.0, value=10.0, step=1.0)
    max_open_trades = st.slider("จำนวนเหรียญที่ถือสูงสุดพร้อมกัน", min_value=1, max_value=5, value=3)

    st.divider()
    st.subheader("🔍 ตั้งค่ากลยุทธ์สแกนเหรียญ")
    timeframe = st.selectbox("กรอบเวลา (Timeframe)", ["1m", "5m", "15m", "1h"], index=1)
    rsi_threshold = st.slider("ความไวของ RSI ตอนย่อตัว", min_value=25, max_value=65, value=45)

    st.divider()
    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        if st.button("▶️ เริ่มบอท", use_container_width=True, type="primary"):
            st.session_state.bot_running = True
            log_event("🟢 เริ่มต้นการทำงานของบอทแล้ว")
            send_telegram_alert(telegram_token, telegram_chat_id, "🤖 <b>Binance Bot Started</b>: บอทเริ่มทำงานและสแกนตลาดแล้วครับ")
    with col_btn2:
        if st.button("⏹️ หยุดบอท", use_container_width=True):
            st.session_state.bot_running = False
            log_event("🔴 หยุดการทำงานของบอทแล้ว")
            send_telegram_alert(telegram_token, telegram_chat_id, "🛑 <b>Binance Bot Stopped</b>: บอทหยุดการทำงานแล้วครับ")

    if st.button("🔄 รีเซ็ตเงินจำลอง ($1,000)", use_container_width=True):
        st.session_state.balance = 1000.0
        st.session_state.initial_balance = 1000.0
        st.session_state.positions = []
        st.session_state.trade_history = []
        st.session_state.logs = []
        save_data()
        st.rerun()

# ==========================================
# 8. หน้าจอหลักแดชบอร์ด (Main UI)
# ==========================================
exchange = init_exchange(trading_mode, api_key, api_secret)

if st.session_state.positions:
    update_and_check_positions(exchange, telegram_token, telegram_chat_id)

unrealized_total = sum(p["Unrealized PnL"] for p in st.session_state.positions)
locked_margin = sum(p["Margin (USDT)"] for p in st.session_state.positions)
total_equity = st.session_state.balance + locked_margin + unrealized_total
net_pnl = total_equity - st.session_state.initial_balance
net_pnl_pct = (net_pnl / st.session_state.initial_balance) * 100

st.title("🤖 Binance Futures AI Scanner & Auto-Trader Pro")
status_badge = "🟢 กำลังทำงาน (สแกนและเทรดอัตโนมัติ)" if st.session_state.bot_running else "🔴 หยุดทำงาน (เฝ้าดูอย่างเดียว)"
st.caption(f"**โหมด:** {trading_mode} | **สถานะ:** {status_badge} | 💾 *บันทึกข้อมูลลงไฟล์อัตโนมัติทุกความเคลื่อนไหว*")

m1, m2, m3, m4 = st.columns(4)
m1.metric("เงินสดคงเหลือ (Available)", f"${st.session_state.balance:,.2f}")
m2.metric("มูลค่าพอร์ตทั้งหมด (Equity)", f"${total_equity:,.2f}", f"{net_pnl:+,.2f} ({net_pnl_pct:+.2f}%)")
m3.metric("กำไร/ขาดทุนที่ยังไม่ปิด (Unrealized PnL)", f"${unrealized_total:+,.2f}")
m4.metric("ออเดอร์ที่เปิดอยู่", f"{len(st.session_state.positions)} / {max_open_trades}")

st.divider()

scan_df = scan_market(exchange, timeframe, rsi_threshold)

if st.session_state.bot_running and not scan_df.empty:
    for _, row in scan_df.iterrows():
        if len(st.session_state.positions) >= max_open_trades:
            break
        if row["Signal"] == "BUY (LONG)":
            open_paper_position(
                row["RawSymbol"], "LONG", row["Price"],
                margin_per_trade, leverage, take_profit_pct, stop_loss_pct,
                telegram_token, telegram_chat_id
            )
        elif row["Signal"] == "SELL (SHORT)":
            open_paper_position(
                row["RawSymbol"], "SHORT", row["Price"],
                margin_per_trade, leverage, take_profit_pct, stop_loss_pct,
                telegram_token, telegram_chat_id
            )

col_chart, col_scan = st.columns([1.4, 1])

with col_chart:
    st.subheader("📊 กราฟราคาแบบเรียลไทม์")
    chart_coin = st.selectbox(
        "เลือกเหรียญเพื่อดูกราฟ",
        WATCHLIST,
        index=WATCHLIST.index(st.session_state.selected_symbol),
        format_func=lambda x: x.replace(":USDT", "")
    )
    st.session_state.selected_symbol = chart_coin

    df_chart = fetch_ohlcv_df(exchange, chart_coin, timeframe=timeframe, limit=80)
    
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df_chart['timestamp'],
        open=df_chart['open'], high=df_chart['high'],
        low=df_chart['low'], close=df_chart['close'],
        name="Price"
    ))
    fig.add_trace(go.Scatter(x=df_chart['timestamp'], y=df_chart['EMA_9'], line=dict(color='cyan', width=1.5), name='EMA 9'))
    fig.add_trace(go.Scatter(x=df_chart['timestamp'], y=df_chart['EMA_21'], line=dict(color='orange', width=1.5), name='EMA 21'))

    for pos in st.session_state.positions:
        if pos["Symbol"] == chart_coin:
            fig.add_hline(y=pos["Entry Price"], line_dash="dot", line_color="white", annotation_text="Entry")
            fig.add_hline(y=pos["TP Price"], line_dash="dash", line_color="lime", annotation_text="TP")
            fig.add_hline(y=pos["SL Price"], line_dash="dash", line_color="red", annotation_text="SL")

    fig.update_layout(
        height=420,
        margin=dict(l=10, r=10, t=20, b=10),
        xaxis_rangeslider_visible=False,
        template="plotly_dark"
    )
    st.plotly_chart(fig, use_container_width=True)

with col_scan:
    st.subheader("📡 ตารางสแกนหาเหรียญอัตโนมัติ")
    if not scan_df.empty:
        display_scan = scan_df[["Symbol", "Price", "RSI (14)", "Signal"]]
        st.dataframe(display_scan, use_container_width=True, height=340, hide_index=True)

    st.caption("ทดลองส่งคำสั่งซื้อขายด้วยมือ:")
    b1, b2 = st.columns(2)
    curr_chart_price = df_chart.iloc[-1]['close']
    if b1.button("🟢 เปิด Long ด้วยมือ", use_container_width=True):
        open_paper_position(chart_coin, "LONG", curr_chart_price, margin_per_trade, leverage, take_profit_pct, stop_loss_pct, telegram_token, telegram_chat_id)
        st.rerun()
    if b2.button("🔴 เปิด Short ด้วยมือ", use_container_width=True):
        open_paper_position(chart_coin, "SHORT", curr_chart_price, margin_per_trade, leverage, take_profit_pct, stop_loss_pct, telegram_token, telegram_chat_id)
        st.rerun()

st.divider()

tab1, tab2, tab3 = st.tabs(["🔥 ออเดอร์ที่เปิดอยู่ (Positions)", "📜 ประวัติการเทรด", "🖥️ บันทึกการทำงานของบอท"])

with tab1:
    if st.session_state.positions:
        pos_df = pd.DataFrame(st.session_state.positions)
        pos_df["Symbol"] = pos_df["Symbol"].str.replace(":USDT", "")
        st.dataframe(
            pos_df[["Symbol", "Side", "Leverage", "Margin (USDT)", "Entry Price", "Current Price", "TP Price", "SL Price", "Unrealized PnL", "ROE %"]],
            use_container_width=True, hide_index=True
        )
        if st.button("🚨 ปิดออเดอร์ทั้งหมดทันที"):
            close_all_positions(telegram_token, telegram_chat_id)
            st.rerun()
    else:
        st.info("ยังไม่มีออเดอร์ที่เปิดอยู่")

with tab2:
    if st.session_state.trade_history:
        st.dataframe(pd.DataFrame(st.session_state.trade_history), use_container_width=True, hide_index=True)
    else:
        st.info("ยังไม่มีประวัติการปิดออเดอร์")

with tab3:
    for log in st.session_state.logs:
        st.text(log)

if st.session_state.bot_running or st.session_state.positions:
    time.sleep(5)
    st.rerun()