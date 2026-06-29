import time
from datetime import datetime, timezone
from dotenv import load_dotenv
import os
import alpaca_trade_api as tradeapi

# Load your keys from .env
load_dotenv()

API_KEY = os.getenv("APCA_API_KEY_ID")
SECRET_KEY = os.getenv("APCA_API_SECRET_KEY")
BASE_URL = os.getenv("APCA_API_BASE_URL")

# Connect to Alpaca
api = tradeapi.REST(API_KEY, SECRET_KEY, BASE_URL, api_version='v2')

# Settings
SYMBOL = "SPY"        # Stock we're trading
TRADE_QTY = 5         # How many shares per trade
MAX_LOSS = 500        # Bot shuts down if we lose $500 in a day

def get_vwap():
    """Get the true daily VWAP calculated from market open"""
    now = datetime.now(timezone.utc)
    market_open = now.replace(hour=13, minute=30, second=0, microsecond=0)
    
    if now < market_open:
        market_open = market_open.replace(day=now.day - 1)
    
    bars = api.get_bars(
        SYMBOL,
        '1Min',
        start=market_open.isoformat(),
        end=now.isoformat(),
        adjustment='raw',
        feed='iex'
    ).df
    
    if bars.empty:
        raise ValueError("No bar data returned")
        
    bars['cum_vol'] = bars['volume'].cumsum()
    bars['cum_vp'] = (bars['vwap'] * bars['volume']).cumsum()
    vwap = bars['cum_vp'].iloc[-1] / bars['cum_vol'].iloc[-1]
    return vwap

def get_price():
    """Get the current market price"""
    trade = api.get_latest_trade(SYMBOL)
    return trade.price

def get_position():
    """Check if we currently own shares"""
    try:
        pos = api.get_position(SYMBOL)
        return int(pos.qty)
    except:
        return 0

def get_daily_pnl():
    """Check how much we've made or lost today"""
    account = api.get_account()
    return float(account.equity) - float(account.last_equity)

def market_is_open():
    """Check if the stock market is currently open"""
    clock = api.get_clock()
    return clock.is_open

def run_bot():
    print("Bot started. Watching", SYMBOL)
    
    while True:
        try:
            if not market_is_open():
                print("Market closed. Waiting...")
                time.sleep(60)
                continue

            pnl = get_daily_pnl()
            if pnl < -MAX_LOSS:
                print(f"Max loss hit (${pnl:.2f}). Shutting down.")
                break

            price = get_price()
            vwap = get_vwap()
            position = get_position()

            print(f"{datetime.now().strftime('%H:%M:%S')} | Price: ${price:.2f} | VWAP: ${vwap:.2f} | Position: {position} shares | P&L: ${pnl:.2f}")

            if price < vwap * 0.999 and position == 0:
                print(f"BUY signal — price below VWAP. Buying {TRADE_QTY} shares.")
                api.submit_order(
                    symbol=SYMBOL,
                    qty=TRADE_QTY,
                    side='buy',
                    type='market',
                    time_in_force='day'
                )

            elif price > vwap * 1.001 and position > 0:
                print(f"SELL signal — price above VWAP. Selling {position} shares.")
                api.submit_order(
                    symbol=SYMBOL,
                    qty=position,
                    side='sell',
                    type='market',
                    time_in_force='day'
                )

            time.sleep(30)

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(30)

run_bot()