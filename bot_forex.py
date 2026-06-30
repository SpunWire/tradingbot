"""
EUR/USD VWAP reversion bot — SCAFFOLDED, NOT YET CONNECTED TO A LIVE BROKER.

Same entry tracking and 2:1 stop loss / take profit logic as bot_spy and bot_crypto.
Stop loss: 0.2% against entry | Take profit: 0.4% in favor (exactly 2×).

TODO: Replace all placeholder functions below with your forex broker's API calls.
      Once connected, this bot registers with shared_risk.py the same way as the
      other bots — no other changes needed to the risk module.
"""

import csv
import time
from datetime import datetime, timezone
from pathlib import Path
import shared_risk as risk

SYMBOL          = "EUR/USD"
TRADE_QTY       = 1_000        # 1 micro-lot — adjust to your broker's minimum
BOT_NAME        = "forex_bot"

STOP_LOSS_PCT   = 0.002        # 0.2% against entry
TAKE_PROFIT_PCT = 0.004        # 0.4% in favor of entry — exactly 2× stop loss

CSV_LOG = "forex_trades_log.csv"


# ── placeholder broker functions ──────────────────────────────────────────────
# TODO: connect to forex broker API (e.g. OANDA, Interactive Brokers, MetaTrader)

def get_price() -> float:
    # TODO: return float(broker.get_bid_ask(SYMBOL).mid)
    raise NotImplementedError("get_price() not connected to a broker yet")


def get_vwap() -> float:
    # TODO: pull 1-min bars from session open (e.g. London 08:00 UTC or NY 13:30 UTC)
    # and compute vwap = sum(bar.vwap * bar.volume) / sum(bar.volume)
    raise NotImplementedError("get_vwap() not connected to a broker yet")


def get_position() -> float:
    # TODO: return current net position in units (positive = long, 0 = flat)
    # e.g. return float(broker.get_position(SYMBOL).units)
    raise NotImplementedError("get_position() not connected to a broker yet")


def get_account_data():
    # TODO: return (equity: float, daily_pnl: float)
    # e.g. acct = broker.get_account(); return float(acct.NAV), float(acct.unrealizedPL)
    raise NotImplementedError("get_account_data() not connected to a broker yet")


def submit_order(side: str, qty: float) -> None:
    # TODO: broker.submit_market_order(symbol=SYMBOL, units=qty if side=='buy' else -qty)
    raise NotImplementedError("submit_order() not connected to a broker yet")


def close_position_cleanly() -> None:
    # TODO: broker.close_position(SYMBOL)
    raise NotImplementedError("close_position_cleanly() not connected to a broker yet")


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
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            BOT_NAME, SYMBOL, qty,
            f"{entry:.6f}", f"{exit_p:.6f}",
            reason,
            f"{realized_pnl:.4f}",
            f"{rr_ratio:.3f}",
        ])


def weekly_summary() -> None:
    if not Path(CSV_LOG).exists():
        print(f"[FOREX] No trade history in {CSV_LOG} yet.")
        return
    with open(CSV_LOG, newline="") as f:
        trades = [r for r in csv.DictReader(f) if r["bot"] == BOT_NAME]
    if not trades:
        print("[FOREX] No closed trades logged yet.")
        return
    total    = len(trades)
    wins     = sum(1 for t in trades if t["exit_reason"] == "take_profit")
    win_rate = wins / total
    avg_rr   = sum(float(t["rr_ratio"]) for t in trades) / total
    breakeven = 1 / (1 + 2.0)
    print(f"\n[FOREX] ══ TRADE SUMMARY ({CSV_LOG}) ═══════════")
    print(f"[FOREX]   Total trades     : {total}")
    print(f"[FOREX]   Wins / Losses    : {wins} / {total - wins}")
    print(f"[FOREX]   Win rate         : {win_rate:.1%}  (breakeven at 2:1 = {breakeven:.1%})")
    print(f"[FOREX]   Avg realized R:R : {avg_rr:+.3f}")
    print(f"[FOREX]   Strategy         : {'PROFITABLE' if win_rate > breakeven else 'UNDER BREAKEVEN — review signals'}")
    print(f"[FOREX] ══════════════════════════════════════════\n")


# ── main loop — mirrors bot_spy / bot_crypto exactly once broker is connected ─

def run_bot() -> None:
    print(f"[FOREX] Bot started — {SYMBOL}, {TRADE_QTY} units/signal.")
    print(f"[FOREX] Exit rules: SL={STOP_LOSS_PCT:.1%} | TP={TAKE_PROFIT_PCT:.1%} (2:1 R:R)")
    print("[FOREX] NOTE: broker functions are placeholders — bot will raise NotImplementedError")
    weekly_summary()

    entry_price = None
    entry_time  = None

    while True:
        try:
            equity, daily_pnl = get_account_data()

            halt, reason = risk.update_and_check(BOT_NAME, daily_pnl, equity)
            if halt:
                print(f"[FOREX] HALT: {reason}")
                close_position_cleanly()
                break

            price    = get_price()
            vwap     = get_vwap()
            position = get_position()
            now_utc  = datetime.now(timezone.utc)

            sl_level = f"{entry_price * (1 - STOP_LOSS_PCT):.5f}"  if entry_price else "—"
            tp_level = f"{entry_price * (1 + TAKE_PROFIT_PCT):.5f}" if entry_price else "—"

            print(
                f"[FOREX] {now_utc.strftime('%H:%M:%S')} | "
                f"Price={price:.5f} | VWAP={vwap:.5f} | "
                f"Pos={position} | SL={sl_level} | TP={tp_level} | P&L={daily_pnl:+.2f}"
            )

            # ── EXIT checks ───────────────────────────────────────────────────
            if position > 0 and entry_price is not None:

                # Stop loss — fires immediately
                if price <= entry_price * (1 - STOP_LOSS_PCT):
                    realized_pnl = (price - entry_price) * TRADE_QTY
                    print(
                        f"[FOREX] SELL — STOP LOSS hit. "
                        f"Entry: {entry_price:.5f} | Exit: {price:.5f} | "
                        f"P&L: {realized_pnl:+.4f}"
                    )
                    halt, reason = risk.update_and_check(BOT_NAME, daily_pnl, equity)
                    if not halt:
                        submit_order("sell", position)
                        log_trade_csv(entry_price, price, "stop_loss", TRADE_QTY)
                    entry_price = None
                    entry_time  = None
                    if halt:
                        print(f"[FOREX] HALT: {reason}")
                        close_position_cleanly()
                        break

                # Take profit — respects 60s minimum
                elif price >= entry_price * (1 + TAKE_PROFIT_PCT):
                    hold_secs = (now_utc - entry_time).total_seconds() if entry_time else 0
                    if hold_secs < 60:
                        print(f"[FOREX] Take profit target reached — holding ({hold_secs:.0f}s / 60s minimum)")
                    else:
                        realized_pnl = (price - entry_price) * TRADE_QTY
                        print(
                            f"[FOREX] SELL — TAKE PROFIT hit. "
                            f"Entry: {entry_price:.5f} | Exit: {price:.5f} | "
                            f"P&L: {realized_pnl:+.4f} (2:1 target achieved)"
                        )
                        halt, reason = risk.update_and_check(BOT_NAME, daily_pnl, equity)
                        if halt:
                            print(f"[FOREX] HALT: {reason}")
                            close_position_cleanly()
                            break
                        submit_order("sell", position)
                        log_trade_csv(entry_price, price, "take_profit", TRADE_QTY)
                        entry_price = None
                        entry_time  = None

            # ── BUY signal ────────────────────────────────────────────────────
            elif price < vwap * 0.999 and position == 0:
                halt, reason = risk.update_and_check(BOT_NAME, daily_pnl, equity)
                if halt:
                    print(f"[FOREX] HALT before buy: {reason}")
                    break
                sl = price * (1 - STOP_LOSS_PCT)
                tp = price * (1 + TAKE_PROFIT_PCT)
                print(f"[FOREX] BUY {TRADE_QTY} units @ {price:.5f} | SL={sl:.5f} | TP={tp:.5f}")
                submit_order("buy", TRADE_QTY)
                entry_price = price
                entry_time  = now_utc
                log_trade_csv(entry_price, price, "buy_open", TRADE_QTY)

            time.sleep(30)

        except NotImplementedError as e:
            print(f"[FOREX] NOT CONNECTED: {e}")
            print("[FOREX] Wire up the broker functions at the top of this file to activate.")
            break
        except Exception as e:
            print(f"[FOREX] Error: {e}")
            time.sleep(30)


if __name__ == "__main__":
    run_bot()
