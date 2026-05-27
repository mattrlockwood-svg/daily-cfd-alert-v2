import yfinance as yf
import pandas as pd
import pandas_ta as ta
import os
import asyncio
from telegram import Bot

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# 🔴 TESTING MODE TOGGLE 🔴
# Set to True to bypass the live scan and immediately send a mock batched Telegram alert.
# Set to False for live production EOD scanning.
TEST_MODE = True 

async def send_batched_telegram_alert(message_text):
    """
    Sends a message via Telegram. If the message exceeds the API character limit,
    it intelligently batches the output into smaller chunks.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing. Skipping alert.")
        return

    bot = Bot(token=TELEGRAM_TOKEN)
    max_chars = 4000  # Kept safely under Telegram's 4096 limit to account for Markdown formatting
    
    # Fast execution if under the limit
    if len(message_text) <= max_chars:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message_text, parse_mode='Markdown')
        return

    # Batching logic for long text
    chunks = []
    while len(message_text) > max_chars:
        # Attempt to split at the last double newline for a clean paragraph break
        split_idx = message_text.rfind('\n\n', 0, max_chars)
        
        if split_idx == -1:
            # Fallback to single newline
            split_idx = message_text.rfind('\n', 0, max_chars)
            if split_idx == -1:
                # Hard split if it's an unbroken block of text
                split_idx = max_chars
                
        chunks.append(message_text[:split_idx])
        message_text = message_text[split_idx:].lstrip()
        
    chunks.append(message_text)

    # Dispatch chunks sequentially with a rate-limit buffer
    for i, chunk in enumerate(chunks):
        try:
            if i > 0:
                chunk = f"*(Continuation {i+1}/{len(chunks)})*\n\n" + chunk
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=chunk, parse_mode='Markdown')
            await asyncio.sleep(1.5) # Protects against HTTP 429 Too Many Requests
        except Exception as e:
            print(f"Failed to send chunk {i+1}: {e}")

def format_signal_message(signal):
    """Formats the raw dictionary into the required JSON/Markdown alert schema."""
    return (
        f"🛡️ **AEGIS EOD SIGNAL TRIGGERED**\n\n"
        f"```json\n"
        f"{{\n"
        f"  \"Alert Type\": \"EOD Swing Signal\",\n"
        f"  \"Timestamp\": \"POST_MARKET_CLOSE_16:00_EST\",\n"
        f"  \"Ticker_Symbol\": \"{signal['Ticker']}\",\n"
        f"  \"Direction\": \"LONG\",\n"
        f"  \"Order Type\": \"LIMIT\",\n"
        f"  \"Entry_Trigger_Price\": \"${signal['Close_Price']}\",\n"
        f"  \"Invalidation SL\": \"${signal['Stop_Loss']}\",\n"
        f"  \"Take Profit TP\": \"DYNAMIC CLOSE ABOVE SMA (5)\",\n"
        f"  \"Time_Stop_Expiration\": \"10_TRADING_SESSIONS\"\n"
        f"}}\n"
        f"```\n"
        f"📊 **Metrics:** RSI(2): {signal['RSI_2']} | RSM: {signal['RSM']}"
    )

def get_sp500_tickers():
    return ['AAPL', 'MSFT', 'NVDA', 'META', 'AMZN', 'GOOGL', 'TSLA', 'JPM', 'V', 'WMT', 'AMD', 'NFLX', 'COST', 'DIS']

def calculate_mansfield_rs(stock_df, spy_df, period=200):
    df = stock_df.join(spy_df['Close'], rsuffix='_SPY', how='inner')
    df['RSD'] = (df['Close'] / df['Close_SPY']) * 100
    df['RSD_SMA200'] = ta.sma(df['RSD'], length=period)
    df['RSM'] = ((df['RSD'] / df['RSD_SMA200']) - 1) * 100
    return df['RSM']

async def run_eod_scan():
    valid_signals = []

    if TEST_MODE:
        print("TEST MODE ENABLED: Bypassing live market scan.")
        # Inject a mock signal
        valid_signals.append({
            "Ticker": "MOCK_TEST",
            "Close_Price": 150.00,
            "RSI_2": 3.14,
            "RSM": 2.50,
            "Stop_Loss": 140.00
        })
        
        # Inject a massive payload to test the batching chunker
        long_text = format_signal_message(valid_signals[0])
        massive_payload = "🔧 **TESTING BATCH LOGIC** 🔧\n\n" + (long_text + "\n\n") * 15
        print("Dispatching oversized test payload to Telegram...")
        await send_batched_telegram_alert(massive_payload)
        return

    tickers = get_sp500_tickers()
    spy = yf.download('SPY', period='1y', progress=False)

    print(f"Executing Aegis EOD Scan on {len(tickers)} equities...")
    
    for ticker in tickers:
        try:
            df = yf.download(ticker, period='1y', progress=False)
            if df.empty or len(df) < 200:
                continue

            df['SMA_200'] = ta.sma(df['Close'], length=200)
            df['RSI_2'] = ta.rsi(df['Close'], length=2)
            df['ATR_14'] = ta.atr(df['High'], df['Low'], df['Close'], length=14)
            df['RSM'] = calculate_mansfield_rs(df, spy)
            df['ADV_50'] = ta.sma(df['Volume'], length=50)

            latest = df.iloc[-1]

            # Structural Edge Filters
            is_liquid = latest['ADV_50'] > 2000000 and latest['Close'] > 20.00
            volatility_threshold = latest['Close'] * 0.025
            is_volatile = latest['ATR_14'] > volatility_threshold
            is_uptrend = latest['Close'] > latest['SMA_200']
            is_market_leader = latest['RSM'] > 0
            is_capitulation = latest['RSI_2'] < 5

            if is_liquid and is_volatile and is_uptrend and is_market_leader and is_capitulation:
                stop_loss = latest['Low'] - (1.5 * latest['ATR_14'])
                signal_data = {
                    "Ticker": ticker,
                    "Close_Price": round(latest['Close'], 2),
                    "RSI_2": round(latest['RSI_2'], 2),
                    "RSM": round(latest['RSM'], 2),
                    "Stop_Loss": round(stop_loss, 2)
                }
                valid_signals.append(signal_data)
                
        except Exception as e:
            continue

    if valid_signals:
        # Sort by lowest RSI(2) and highest RSM to manage sector heat caps
        valid_signals = sorted(valid_signals, key=lambda x: (x['RSI_2'], -x['RSM']))[:5]
        
        for s in valid_signals:
            msg = format_signal_message(s)
            await send_batched_telegram_alert(msg)
            print(f"Alert dispatched for {s['Ticker']}")
    else:
        print("Scan complete. 0 valid signals triggered today. Free margin protected.")

if __name__ == "__main__":
    asyncio.run(run_eod_scan())
