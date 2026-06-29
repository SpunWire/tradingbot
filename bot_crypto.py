"""
BTC/USD VWAP reversion bot — trades 24/7, VWAP resets at midnight UTC.
Logs every trade to crypto_trades.log.
Imports shared_risk for combined P&L monitoring with bot_spy.
"""

import time
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv
import os
import alpaca_trade_api as tradeapi
import pandas as pd
import shared_risk as risk

load_dotenv(".env.crypto")

API_KEY    = os.getenv("APCA_API_KEY_ID")
SECRET_KEY = os.getenv("APCA_API_SECRET_KEY")
BASE_URL   = os.getenv("APCA_API_BASE_URL")

api = tradeapi.REST(API_KEY, SECRET_KEY, BASE_URL, api_version='v2')

SYMBOL          = "BTC/USD"
POSITION_SYMBOL = "BTCUSD"   # How Alpaca identifies it in the positions endpoint
TRADE_QTY       = 0.01       # BTC per signal (~$600–$1,000); 3 full BTC would exceed $25k account
BOT_NAME        = "crypto_bot"

logging.basicConfig(
    filename="crypto_trades.log",
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# ── market data ───────────────────────────────────────────────────────────────

def midnight_utc() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalise_bars(bars) -> pd.DataFrame:
    """Drop the symbol level from a MultiIndex so callers get a flat DataFrame."""
    if isinstance(bars.index, pd.MultiIndex):
        try:
            bars = bars.xs(SYMBOL, level=0)
        except KeyError:
            bars = bars.droplevel(0)
    return bars


def get_price_and_vwap():
    """
    Pull 1-min bars from midnight UTC to now and return (price, vwap).
    Price is taken from the most recent bar's close — no separate API call needed.
    Returns (None, None) when no bars are available yet.
    """
    start = midnight_utc()
    bars = _normalise_bars(api.get_crypto_bars(SYMBOL, "1Min", start=start).df)

    if bars.empty:
        return None, None

    price = float(bars["close"].iloc[-1])
    vwap  = float((bars["vwap"] * bars["volume"]).sum() / bars["volume"].sum())
    return price, vwap


def get_position() -> float:
    try:
        return float(api.get_position(POSITION_SYMBOL).qty)
    except Exception:
        return 0.0


def get_account_data():
    acct = api.get_account()
    equity    = float(acct.equity)
    daily_pnl = equity - float(acct.last_equity)
    return equity, daily_pnl


# ── order management ──────────────────────────────────────────────────────────

def submit_order(side: str, qty: float) -> None:
    api.submit_order(
        symbol=SYMBOL,
        qty=qty,
        side=side,
        type="market",
        time_in_force="gtc",   # crypto requires gtc, not day
    )


def close_position_cleanly() -> None:
    pos = get_position()
    if pos > 0:
        print(f"[CRYPTO] Closing {pos} BTC before halt.")
        try:
            submit_order("sell", pos)
            logging.info("CLOSE | %s | qty=%s | reason=halt", SYMBOL, pos)
        except Exception as e:
            print(f"[CRYPTO] Error closing position: {e}")


# ── main loop ─────────────────────────────────────────────────────────────────

def run_bot() -> None:
    print(f"[CRYPTO] Bot started — {SYMBOL}, {TRADE_QTY} BTC/signal.")
    entry_time = None

    while True:
        try:
            equity, daily_pnl = get_account_data()

            halt, reason = risk.update_and_check(BOT_NAME, daily_pnl, equity)
            if halt:
                print(f"[CRYPTO] HALT: {reason}")
                close_position_cleanly()
                break

            price, vwap = get_price_and_vwap()
            if price is None or vwap is None:
                print("[CRYPTO] No bar data yet — waiting 30s.")
                time.sleep(30)
                continue

            position = get_position()
            now_utc  = datetime.now(timezone.utc)

            print(
                f"[CRYPTO] {now_utc.strftime('%H:%M:%S')} | "
                f"Price=${price:,.2f} | VWAP=${vwap:,.2f} | "
                f"Pos={position} BTC | P&L={daily_pnl:+.2f}"
            )

            # ── BUY signal ────────────────────────────────────────────────────
            if price < vwap * 0.999 and position == 0:
                halt, reason = risk.update_and_check(BOT_NAME, daily_pnl, equity)
                if halt:
                    print(f"[CRYPTO] HALT before buy: {reason}")
                    break
                print(f"[CRYPTO] BUY  {TRADE_QTY} BTC @ ${price:,.2f}")
                submit_order("buy", TRADE_QTY)
                entry_time = now_utc
                logging.info(
                    "BUY | %s | qty=%s | price=%.2f | vwap=%.2f | equity=%.2f | pnl=%+.2f",
                    SYMBOL, TRADE_QTY, price, vwap, equity, daily_pnl,
                )

            # ── SELL signal ───────────────────────────────────────────────────
            elif price > vwap * 1.001 and position > 0:
                hold_secs = (now_utc - entry_time).total_seconds() if entry_time else 0
                if hold_secs < 60:
                    print(f"[CRYPTO] SELL signal — holding ({hold_secs:.0f}s / 60s minimum)")
                else:
                    halt, reason = risk.update_and_check(BOT_NAME, daily_pnl, equity)
                    if halt:
                        print(f"[CRYPTO] HALT before sell: {reason}")
                        close_position_cleanly()
                        break
                    print(f"[CRYPTO] SELL {position} BTC @ ${price:,.2f}")
                    submit_order("sell", position)
                    entry_time = None
                    logging.info(
                        "SELL | %s | qty=%s | price=%.2f | vwap=%.2f | equity=%.2f | pnl=%+.2f",
                        SYMBOL, position, price, vwap, equity, daily_pnl,
                    )

            time.sleep(30)

        except Exception as e:
            print(f"[CRYPTO] Error: {e}")
            time.sleep(30)


if __name__ == "__main__":
    run_bot()
