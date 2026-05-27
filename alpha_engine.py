import yfinance as yf
import pandas as pd
import pandas_ta as ta
import os
import asyncio
from telegram import Bot
from datetime import datetime

# --- TELEGRAM BOT SETUP ---
# These will be pulled securely from GitHub Secrets
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

async def send_telegram_alert(signal):
    """Formats and sends the exact EOD execution schema via Telegram."""
    bot = Bot(token=TELEGRAM_TOKEN)
    
    message = (
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
    
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode='Markdown')

def get_sp500_tickers():
    # Expanding the test list for broader market coverage
    return ['AAPL', 'MSFT', 'NVDA', 'META', 'AMZN', 'GOOGL', 'TSLA', 'JPM', 'V', 'WMT', 'AMD', 'NFLX', 'COST', 'DIS']

def calculate_mansfield_rs(stock_df, spy_df, period=200):
    df = stock_df.join(spy_df['Close'], rsuffix='_SPY', how='inner')
    df['RSD'] = (df['Close'] / df['Close_SPY']) * 100
    df['RSD_SMA200'] = ta.sma(df['RSD'], length=period)
    df['RSM'] = ((df['RSD'] / df['RSD_SMA200']) - 1) * 100
    return df['RSM']

async def run_eod_scan():
    tickers = get_sp500_tickers()
    spy = yf.download('SPY', period='1y', progress=False)
    valid_signals = []

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
            await send_telegram_alert(s)
            print(f"Alert dispatched for {s['Ticker']}")
    else:
        print("Scan complete. 0 valid signals triggered today. Free margin protected.")

if __name__ == "__main__":
    # Standard asyncio run for the Telegram bot wrapper
    asyncio.run(run_eod_scan())
