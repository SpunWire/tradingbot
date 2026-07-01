# ============================================================
# DISPLAY TIMEZONE: EDT (America/New_York) — DO NOT CHANGE
# Internal reset logic uses UTC — that stays UTC
# datetime.now(EDT) for all terminal prints and CSV logs
# datetime.utcnow() only inside reset window comparisons
# ============================================================

import csv
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv
import os
import pytz
import alpaca_trade_api as tradeapi

load_dotenv()

EDT = pytz.timezone("America/New_York")

API_KEY    = os.getenv("APCA_API_KEY_ID")
SECRET_KEY = os.getenv("APCA_API_SECRET_KEY")
BASE_URL   = os.getenv("APCA_API_BASE_URL")

api = tradeapi.REST(API_KEY, SECRET_KEY, BASE_URL, api_version='v2')

SYMBOL          = "SPY"
TRADE_QTY       = 5
MAX_DAILY_LOSS  = 500

STOP_LOSS_PCT   = 0.003   # 0.3% against entry
TAKE_PROFIT_PCT = 0.006   # 0.6% in favor of entry — exactly 2× stop loss

CSV_LOG = "bot_trades_log.csv"


def log_trade_csv(entry: float, exit_p: float, reason: str, qty: int, direction: str) -> None:
    if direction == "long":
        realized_pnl = (exit_p - entry) * qty
        rr_ratio     = (exit_p - entry) / (entry * STOP_LOSS_PCT)
    else:  # short
        realized_pnl = (entry - exit_p) * qty
        rr_ratio     = (entry - exit_p) / (entry * STOP_LOSS_PCT)
    exists = Path(CSV_LOG).exists()
    with open(CSV_LOG, "a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["timestamp", "bot", "symbol", "qty", "direction",
                        "entry_price", "exit_price", "exit_reason",
                        "realized_pnl", "rr_ratio"])
        w.writerow([
            datetime.now(EDT).strftime("%Y-%m-%d %H:%M:%S ET"),
            "bot", SYMBOL, qty, direction,
            f"{entry:.4f}", f"{exit_p:.4f}",
            reason,
            f"{realized_pnl:.2f}",
            f"{rr_ratio:.3f}",
        ])


def weekly_summary() -> None:
    if not Path(CSV_LOG).exists():
        print(f"No trade history in {CSV_LOG} yet.")
        return
    with open(CSV_LOG, newline="") as f:
        trades = [r for r in csv.DictReader(f) if r["bot"] == "bot"]
    if not trades:
        print("No closed trades logged yet.")
        return
    total     = len(trades)
    wins      = sum(1 for t in trades if t["exit_reason"] in ("take_profit", "short_take_profit"))
    win_rate  = wins / total
    avg_rr    = sum(float(t["rr_ratio"]) for t in trades) / total
    breakeven = 1 / (1 + 2.0)
    print(f"\n══ TRADE SUMMARY ({CSV_LOG}) ══════════════════")
    print(f"  Total trades     : {total}")
    print(f"  Wins / Losses    : {wins} / {total - wins}")
    print(f"  Win rate         : {win_rate:.1%}  (breakeven at 2:1 = {breakeven:.1%})")
    print(f"  Avg realized R:R : {avg_rr:+.3f}")
    print(f"  Strategy         : {'PROFITABLE' if win_rate > breakeven else 'UNDER BREAKEVEN — review signals'}")
    print(f"═══════════════════════════════════════════════\n")


def get_vwap():
    now = datetime.now(timezone.utc)
    market_open = now.replace(hour=13, minute=30, second=0, microsecond=0)
    if now < market_open:
        market_open = market_open.replace(day=now.day - 1)
    bars = api.get_bars(
        SYMBOL, '1Min',
        start=market_open.isoformat(),
        end=now.isoformat(),
        adjustment='raw',
        feed='iex',
    ).df
    if bars.empty:
        return None
    bars['cum_vol'] = bars['volume'].cumsum()
    bars['cum_vp']  = (bars['vwap'] * bars['volume']).cumsum()
    return float(bars['cum_vp'].iloc[-1] / bars['cum_vol'].iloc[-1])


def get_price() -> float:
    return float(api.get_latest_trade(SYMBOL).price)


def get_position() -> int:
    try:
        return int(api.get_position(SYMBOL).qty)
    except Exception:
        return 0


def get_daily_pnl() -> float:
    acct = api.get_account()
    return float(acct.equity) - float(acct.last_equity)


def market_is_open() -> bool:
    return api.get_clock().is_open


def run_bot():
    print(f"Bot started. Watching {SYMBOL}")
    print(f"Exit rules: SL={STOP_LOSS_PCT:.1%} | TP={TAKE_PROFIT_PCT:.1%} (2:1 R:R)")
    weekly_summary()

    entry_price = None
    entry_time  = None

    while True:
        try:
            if not market_is_open():
                print("Market closed. Waiting...")
                time.sleep(60)
                continue

            pnl = get_daily_pnl()
            if pnl < -MAX_DAILY_LOSS:
                print(f"Max daily loss hit (${pnl:.2f}). Shutting down.")
                break

            price    = get_price()
            vwap     = get_vwap()
            position = get_position()
            now_utc  = datetime.now(timezone.utc)
            now_et   = datetime.now(EDT)

            if position > 0 and entry_price:
                sl_level = f"${entry_price * (1 - STOP_LOSS_PCT):.2f}"
                tp_level = f"${entry_price * (1 + TAKE_PROFIT_PCT):.2f}"
            elif position < 0 and entry_price:
                sl_level = f"${entry_price * (1 + STOP_LOSS_PCT):.2f}"
                tp_level = f"${entry_price * (1 - TAKE_PROFIT_PCT):.2f}"
            else:
                sl_level = tp_level = "—"

            if position > 0:
                pos_display = f"+{position} {SYMBOL} (LONG)"
            elif position < 0:
                pos_display = f"{position} {SYMBOL} (SHORT)"
            else:
                pos_display = "0"

            print(
                f"{now_et.strftime('%H:%M:%S ET')} | "
                f"Price: ${price:.2f} | VWAP: {f'${vwap:.2f}' if vwap else 'N/A'} | "
                f"Pos: {pos_display} | SL: {sl_level} | TP: {tp_level} | P&L: ${pnl:.2f}"
            )

            # ── LONG exit checks ──────────────────────────────────────────────
            if position > 0 and entry_price is not None:

                if price <= entry_price * (1 - STOP_LOSS_PCT):
                    realized_pnl = (price - entry_price) * TRADE_QTY
                    print(f"SELL — STOP LOSS hit. Entry: ${entry_price:.2f} | Exit: ${price:.2f} | P&L: {realized_pnl:+.2f}")
                    api.submit_order(symbol=SYMBOL, qty=position, side='sell',
                                     type='market', time_in_force='day')
                    log_trade_csv(entry_price, price, "stop_loss", TRADE_QTY, "long")
                    entry_price = None
                    entry_time  = None

                elif price >= entry_price * (1 + TAKE_PROFIT_PCT):
                    hold_secs = (now_utc - entry_time).total_seconds() if entry_time else 0
                    if hold_secs < 60:
                        print(f"Take profit target reached — holding ({hold_secs:.0f}s / 60s minimum)")
                    else:
                        realized_pnl = (price - entry_price) * TRADE_QTY
                        print(f"SELL — TAKE PROFIT hit. Entry: ${entry_price:.2f} | Exit: ${price:.2f} | P&L: {realized_pnl:+.2f} (2:1 achieved)")
                        api.submit_order(symbol=SYMBOL, qty=position, side='sell',
                                         type='market', time_in_force='day')
                        log_trade_csv(entry_price, price, "take_profit", TRADE_QTY, "long")
                        entry_price = None
                        entry_time  = None

            # ── SHORT exit checks ─────────────────────────────────────────────
            elif position < 0 and entry_price is not None:

                if price >= entry_price * (1 + STOP_LOSS_PCT):
                    realized_pnl = (entry_price - price) * TRADE_QTY
                    print(f"COVER — STOP LOSS hit. Entry: ${entry_price:.2f} | Exit: ${price:.2f} | P&L: {realized_pnl:+.2f}")
                    api.submit_order(symbol=SYMBOL, qty=abs(position), side='buy',
                                     type='market', time_in_force='day')
                    log_trade_csv(entry_price, price, "short_stop_loss", TRADE_QTY, "short")
                    entry_price = None
                    entry_time  = None

                elif price <= entry_price * (1 - TAKE_PROFIT_PCT):
                    hold_secs = (now_utc - entry_time).total_seconds() if entry_time else 0
                    if hold_secs < 60:
                        print(f"Short take profit reached — holding ({hold_secs:.0f}s / 60s minimum)")
                    else:
                        realized_pnl = (entry_price - price) * TRADE_QTY
                        print(f"COVER — TAKE PROFIT hit. Entry: ${entry_price:.2f} | Exit: ${price:.2f} | P&L: {realized_pnl:+.2f} (2:1 achieved)")
                        api.submit_order(symbol=SYMBOL, qty=abs(position), side='buy',
                                         type='market', time_in_force='day')
                        log_trade_csv(entry_price, price, "short_take_profit", TRADE_QTY, "short")
                        entry_price = None
                        entry_time  = None

            # ── ENTRY signals — only when flat ────────────────────────────────
            elif vwap is not None and position == 0:

                if price < vwap * 0.999:
                    sl = price * (1 - STOP_LOSS_PCT)
                    tp = price * (1 + TAKE_PROFIT_PCT)
                    print(f"BUY {TRADE_QTY} shares @ ${price:.2f} | SL=${sl:.2f} | TP=${tp:.2f}")
                    api.submit_order(symbol=SYMBOL, qty=TRADE_QTY, side='buy',
                                     type='market', time_in_force='day')
                    entry_price = price
                    entry_time  = now_utc

                elif price > vwap * 1.001:
                    sl = price * (1 + STOP_LOSS_PCT)
                    tp = price * (1 - TAKE_PROFIT_PCT)
                    print(f"SHORT signal — price above VWAP. Shorting {TRADE_QTY} shares @ ${price:.2f} | SL=${sl:.2f} | TP=${tp:.2f}")
                    api.submit_order(symbol=SYMBOL, qty=TRADE_QTY, side='sell',
                                     type='market', time_in_force='day')
                    entry_price = price
                    entry_time  = now_utc

            time.sleep(30)

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(30)


run_bot()
