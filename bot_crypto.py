"""
BTC/USD VWAP reversion bot — trades 24/7, VWAP resets at midnight UTC.
Exit logic: fixed 2:1 stop loss / take profit based on entry price.
VWAP is used only as the BUY trigger; it does NOT drive exits.
Logs every closed trade to crypto_trades_log.csv.
"""

import csv
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv
import os
import pytz
import alpaca_trade_api as tradeapi
import pandas as pd
import shared_risk as risk

load_dotenv(".env.crypto")

EDT = pytz.timezone("America/New_York")

API_KEY    = os.getenv("APCA_API_KEY_ID")
SECRET_KEY = os.getenv("APCA_API_SECRET_KEY")
BASE_URL   = os.getenv("APCA_API_BASE_URL")

api = tradeapi.REST(API_KEY, SECRET_KEY, BASE_URL, api_version='v2')

SYMBOL          = "BTC/USD"
POSITION_SYMBOL = "BTCUSD"
TRADE_QTY       = 0.01
BOT_NAME        = "crypto_bot"

STOP_LOSS_PCT   = 0.005   # 0.5% against entry
TAKE_PROFIT_PCT = 0.010   # 1.0% in favor of entry — exactly 2× stop loss

CSV_LOG = "crypto_trades_log.csv"


# ── trade logging ─────────────────────────────────────────────────────────────

def log_trade_csv(entry: float, exit_p: float, reason: str, qty: float) -> None:
    realized_pnl = (exit_p - entry) * qty
    rr_ratio     = (exit_p - entry) / (entry * STOP_LOSS_PCT)
    exists        = Path(CSV_LOG).exists()
    with open(CSV_LOG, "a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["timestamp", "bot", "symbol", "qty",
                        "entry_price", "exit_price", "exit_reason",
                        "realized_pnl", "rr_ratio"])
        w.writerow([
            datetime.now(EDT).strftime("%Y-%m-%d %H:%M:%S ET"),
            BOT_NAME, SYMBOL, qty,
            f"{entry:.4f}", f"{exit_p:.4f}",
            reason,
            f"{realized_pnl:.4f}",
            f"{rr_ratio:.3f}",
        ])


def weekly_summary() -> None:
    if not Path(CSV_LOG).exists():
        print(f"[CRYPTO] No trade history in {CSV_LOG} yet.")
        return
    with open(CSV_LOG, newline="") as f:
        trades = [r for r in csv.DictReader(f) if r["bot"] == BOT_NAME]
    if not trades:
        print("[CRYPTO] No closed trades logged yet.")
        return
    total    = len(trades)
    wins     = sum(1 for t in trades if t["exit_reason"] == "take_profit")
    win_rate = wins / total
    avg_rr   = sum(float(t["rr_ratio"]) for t in trades) / total
    breakeven = 1 / (1 + 2.0)
    print(f"\n[CRYPTO] ══ TRADE SUMMARY ({CSV_LOG}) ═════════════")
    print(f"[CRYPTO]   Total trades     : {total}")
    print(f"[CRYPTO]   Wins / Losses    : {wins} / {total - wins}")
    print(f"[CRYPTO]   Win rate         : {win_rate:.1%}  (breakeven at 2:1 = {breakeven:.1%})")
    print(f"[CRYPTO]   Avg realized R:R : {avg_rr:+.3f}")
    print(f"[CRYPTO]   Strategy         : {'PROFITABLE' if win_rate > breakeven else 'UNDER BREAKEVEN — review signals'}")
    print(f"[CRYPTO] ══════════════════════════════════════════\n")


# ── market data ───────────────────────────────────────────────────────────────

def midnight_utc() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalise_bars(bars) -> pd.DataFrame:
    if isinstance(bars.index, pd.MultiIndex):
        try:
            bars = bars.xs(SYMBOL, level=0)
        except KeyError:
            bars = bars.droplevel(0)
    return bars


def get_price_and_vwap():
    start = midnight_utc()
    bars = _normalise_bars(api.get_crypto_bars(SYMBOL, "1Min", start=start).df)
    if bars.empty:
        return None, None
    price = float(bars["close"].iloc[-1])
    vwap  = float((bars["vwap"] * bars["volume"]).sum() / bars["volume"].sum())
    return price, vwap


def get_trend() -> str:
    """
    Pulls last 6 completed 10-min bars and returns 'DOWN' or 'NEUTRAL'.
    DOWN  : last 3 closes are each lower than the previous (3 consecutive lower closes).
    NEUTRAL: anything else, including 2 consecutive higher closes after a downtrend.
    Only used to gate new BUY entries — never affects open position management.
    """
    try:
        start = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        bars  = _normalise_bars(api.get_crypto_bars(SYMBOL, "10Min", start=start).df)
        if bars.empty or len(bars) < 4:
            return "NEUTRAL"
        closes = bars["close"].tolist()
        c = closes[-4:]   # last 4 gives us 3 consecutive comparisons
        if c[3] < c[2] < c[1]:
            return "DOWN"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"


def get_position() -> float:
    try:
        return float(api.get_position(POSITION_SYMBOL).qty)
    except Exception:
        return 0.0


def get_account_data() -> float:
    return float(api.get_account().equity)


# ── order management ──────────────────────────────────────────────────────────

def submit_order(side: str, qty: float) -> None:
    api.submit_order(
        symbol=SYMBOL, qty=qty, side=side,
        type="market", time_in_force="gtc",
    )


def close_position_cleanly() -> None:
    pos = get_position()
    if pos > 0:
        print(f"[CRYPTO] Closing {pos} BTC before halt.")
        try:
            submit_order("sell", pos)
        except Exception as e:
            print(f"[CRYPTO] Error closing position: {e}")


# ── main loop ─────────────────────────────────────────────────────────────────

def run_bot() -> None:
    print(f"[CRYPTO] Bot started — {SYMBOL}, {TRADE_QTY} BTC/signal.")
    print(f"[CRYPTO] Exit rules: SL={STOP_LOSS_PCT:.1%} | TP={TAKE_PROFIT_PCT:.1%} (2:1 R:R)")
    weekly_summary()

    entry_price = None
    entry_time  = None
    trend_state = "NEUTRAL"

    # Recover any position that was open before this restart
    existing = get_position()
    if existing > 0:
        try:
            pos_data    = api.get_position(POSITION_SYMBOL)
            entry_price = float(pos_data.avg_entry_price)
            entry_time  = datetime.now(timezone.utc) - timedelta(seconds=61)  # waive 60s hold
            sl = entry_price * (1 - STOP_LOSS_PCT)
            tp = entry_price * (1 + TAKE_PROFIT_PCT)
            print(f"[CRYPTO] Recovered open position: {existing} BTC @ ${entry_price:,.2f}")
            print(f"[CRYPTO] SL=${sl:,.2f} | TP=${tp:,.2f} — resuming management")
        except Exception as e:
            print(f"[CRYPTO] Warning: open position found but could not recover entry price: {e}")

    while True:
        try:
            equity = get_account_data()

            halt, reason, daily_pnl = risk.update_and_check(BOT_NAME, equity)
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
            now_et   = datetime.now(EDT)

            # Check trend on every iteration (only gates buys, never exits)
            new_trend = get_trend()
            if new_trend != trend_state:
                if new_trend == "DOWN":
                    print("[CRYPTO] Trend filter: DOWNTREND detected — buy signal suppressed")
                else:
                    print("[CRYPTO] Trend filter: NEUTRAL — buy signals resumed")
                trend_state = new_trend

            sl_level = f"${entry_price * (1 - STOP_LOSS_PCT):,.2f}"  if entry_price else "—"
            tp_level = f"${entry_price * (1 + TAKE_PROFIT_PCT):,.2f}" if entry_price else "—"

            print(
                f"[CRYPTO] {now_et.strftime('%H:%M:%S ET')} | "
                f"Price=${price:,.2f} | VWAP=${vwap:,.2f} | "
                f"Pos={position} BTC | Trend={trend_state} | "
                f"SL={sl_level} | TP={tp_level} | P&L={daily_pnl:+.2f}"
            )

            # ── EXIT checks (only when in a position) ─────────────────────────
            if position > 0 and entry_price is not None:

                # Stop loss — fires immediately (capital protection)
                if price <= entry_price * (1 - STOP_LOSS_PCT):
                    realized_pnl = (price - entry_price) * TRADE_QTY
                    print(
                        f"[CRYPTO] SELL — STOP LOSS hit. "
                        f"Entry: ${entry_price:,.2f} | Exit: ${price:,.2f} | "
                        f"P&L: {realized_pnl:+.4f}"
                    )
                    halt, reason, daily_pnl = risk.update_and_check(BOT_NAME, equity)
                    if not halt:
                        submit_order("sell", position)
                        log_trade_csv(entry_price, price, "stop_loss", TRADE_QTY)
                    entry_price = None
                    entry_time  = None
                    if halt:
                        print(f"[CRYPTO] HALT: {reason}")
                        close_position_cleanly()
                        break

                # Take profit — respects 60s minimum hold
                elif price >= entry_price * (1 + TAKE_PROFIT_PCT):
                    hold_secs = (now_utc - entry_time).total_seconds() if entry_time else 0
                    if hold_secs < 60:
                        print(f"[CRYPTO] Take profit target reached — holding for 60s minimum ({hold_secs:.0f}s elapsed)")
                    else:
                        realized_pnl = (price - entry_price) * TRADE_QTY
                        print(
                            f"[CRYPTO] SELL — TAKE PROFIT hit. "
                            f"Entry: ${entry_price:,.2f} | Exit: ${price:,.2f} | "
                            f"P&L: {realized_pnl:+.4f} (2:1 target achieved)"
                        )
                        halt, reason, daily_pnl = risk.update_and_check(BOT_NAME, equity)
                        if halt:
                            print(f"[CRYPTO] HALT: {reason}")
                            close_position_cleanly()
                            break
                        submit_order("sell", position)
                        log_trade_csv(entry_price, price, "take_profit", TRADE_QTY)
                        entry_price = None
                        entry_time  = None

            # Trend suppressed — print once per loop when buy would otherwise fire
            elif price < vwap * 0.999 and position == 0 and trend_state == "DOWN":
                print("[CRYPTO] Trend filter: DOWNTREND detected — buy signal suppressed")

            # ── BUY signal — only when flat, VWAP trigger, trend filter applied ─
            elif price < vwap * 0.999 and position == 0 and trend_state != "DOWN":
                halt, reason, daily_pnl = risk.update_and_check(BOT_NAME, equity)
                if halt:
                    print(f"[CRYPTO] HALT before buy: {reason}")
                    break
                sl = price * (1 - STOP_LOSS_PCT)
                tp = price * (1 + TAKE_PROFIT_PCT)
                print(
                    f"[CRYPTO] BUY {TRADE_QTY} BTC @ ${price:,.2f} | "
                    f"SL=${sl:,.2f} | TP=${tp:,.2f}"
                )
                submit_order("buy", TRADE_QTY)
                entry_price = price
                entry_time  = now_utc

            time.sleep(30)

        except Exception as e:
            print(f"[CRYPTO] Error: {e}")
            time.sleep(30)


if __name__ == "__main__":
    run_bot()
