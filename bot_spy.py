"""
SPY VWAP reversion bot — trades 9:30am–4:00pm ET only.
Logs every trade to spy_trades.log.
Imports shared_risk for combined P&L monitoring with bot_crypto.
"""

import time
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv
import os
import pytz
import alpaca_trade_api as tradeapi
import shared_risk as risk

load_dotenv(".env.spy")

API_KEY    = os.getenv("APCA_API_KEY_ID")
SECRET_KEY = os.getenv("APCA_API_SECRET_KEY")
BASE_URL   = os.getenv("APCA_API_BASE_URL")

api = tradeapi.REST(API_KEY, SECRET_KEY, BASE_URL, api_version='v2')

SYMBOL     = "SPY"
TRADE_QTY  = 3
BOT_NAME   = "spy_bot"
ET         = pytz.timezone("America/New_York")

logging.basicConfig(
    filename="spy_trades.log",
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# ── market data ───────────────────────────────────────────────────────────────

def market_is_open() -> bool:
    return api.get_clock().is_open


def get_vwap():
    now_et = datetime.now(ET)
    market_open_et = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    start = market_open_et.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    bars = api.get_bars(SYMBOL, "1Min", start=start, feed="iex").df
    if bars.empty:
        return None
    return float((bars["vwap"] * bars["volume"]).sum() / bars["volume"].sum())


def get_price() -> float:
    return float(api.get_latest_trade(SYMBOL).price)


def get_position() -> int:
    try:
        return int(api.get_position(SYMBOL).qty)
    except Exception:
        return 0


def get_account_data():
    acct = api.get_account()
    equity    = float(acct.equity)
    daily_pnl = equity - float(acct.last_equity)
    return equity, daily_pnl


# ── order management ──────────────────────────────────────────────────────────

def submit_order(side: str, qty: int) -> None:
    api.submit_order(
        symbol=SYMBOL,
        qty=qty,
        side=side,
        type="market",
        time_in_force="day",
    )


def close_position_cleanly() -> None:
    pos = get_position()
    if pos > 0:
        print(f"[SPY] Closing {pos} shares before halt.")
        try:
            submit_order("sell", pos)
            logging.info("CLOSE | %s | qty=%d | reason=halt", SYMBOL, pos)
        except Exception as e:
            print(f"[SPY] Error closing position: {e}")


# ── main loop ─────────────────────────────────────────────────────────────────

def run_bot() -> None:
    print(f"[SPY] Bot started — {SYMBOL}, {TRADE_QTY} shares/signal.")
    entry_time = None

    while True:
        try:
            if not market_is_open():
                now_et = datetime.now(ET)
                print(f"[SPY] {now_et.strftime('%H:%M:%S ET')} — market closed. Sleeping 60s...")
                time.sleep(60)
                continue

            equity, daily_pnl = get_account_data()

            halt, reason = risk.update_and_check(BOT_NAME, daily_pnl, equity)
            if halt:
                print(f"[SPY] HALT: {reason}")
                close_position_cleanly()
                break

            price = get_price()
            vwap  = get_vwap()
            if vwap is None:
                print("[SPY] VWAP unavailable — not enough bars yet. Waiting 30s.")
                time.sleep(30)
                continue

            position = get_position()
            now_utc  = datetime.now(timezone.utc)

            print(
                f"[SPY] {now_utc.strftime('%H:%M:%S')} | "
                f"Price=${price:.2f} | VWAP=${vwap:.2f} | "
                f"Pos={position} | P&L={daily_pnl:+.2f}"
            )

            # ── BUY signal ────────────────────────────────────────────────────
            if price < vwap * 0.999 and position == 0:
                halt, reason = risk.update_and_check(BOT_NAME, daily_pnl, equity)
                if halt:
                    print(f"[SPY] HALT before buy: {reason}")
                    break
                print(f"[SPY] BUY  {TRADE_QTY} shares @ ${price:.2f}")
                submit_order("buy", TRADE_QTY)
                entry_time = now_utc
                logging.info(
                    "BUY | %s | qty=%d | price=%.2f | vwap=%.2f | equity=%.2f | pnl=%+.2f",
                    SYMBOL, TRADE_QTY, price, vwap, equity, daily_pnl,
                )

            # ── SELL signal ───────────────────────────────────────────────────
            elif price > vwap * 1.001 and position > 0:
                hold_secs = (now_utc - entry_time).total_seconds() if entry_time else 0
                if hold_secs < 60:
                    print(f"[SPY] SELL signal — holding ({hold_secs:.0f}s / 60s minimum)")
                else:
                    halt, reason = risk.update_and_check(BOT_NAME, daily_pnl, equity)
                    if halt:
                        print(f"[SPY] HALT before sell: {reason}")
                        close_position_cleanly()
                        break
                    print(f"[SPY] SELL {position} shares @ ${price:.2f}")
                    submit_order("sell", position)
                    entry_time = None
                    logging.info(
                        "SELL | %s | qty=%d | price=%.2f | vwap=%.2f | equity=%.2f | pnl=%+.2f",
                        SYMBOL, position, price, vwap, equity, daily_pnl,
                    )

            time.sleep(30)

        except Exception as e:
            print(f"[SPY] Error: {e}")
            time.sleep(30)


if __name__ == "__main__":
    run_bot()
