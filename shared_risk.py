"""
Shared risk module — single Alpaca paper account, two bots.

Daily P&L is tracked from a baseline equity snapshot taken at 00:30 UTC each day
to match Velotrade's reset schedule. Alpaca's last_equity field is NOT used because
it resets at market close, not at 00:30 UTC.

On each call, if the current 00:30 UTC period has changed, a new baseline snapshot
is taken from the equity value passed in and the day's P&L resets to zero.

Halt conditions:
  - Daily loss limit   : daily P&L (vs 00:30 UTC baseline) <= -$1,000  →  both bots halt for today
  - Drawdown floor     : account equity < $22,500  →  both bots halt permanently

Returns (should_halt: bool, reason: str, daily_pnl: float) on every call.
Bots must use the returned daily_pnl for display — do not use Alpaca's last_equity.
"""

import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

_STATE_FILE = Path("shared_state.json")
_PERM_HALT  = Path("halt_permanent.flag")

DAILY_LOSS_LIMIT = 1_000.0
DRAWDOWN_FLOOR   = 22_500.0


# ── period helpers ────────────────────────────────────────────────────────────

def _period_id() -> str:
    """
    Returns the start-date of the current 00:30 UTC trading period.
    Before 00:30 UTC → yesterday's date (still inside that period).
    After  00:30 UTC → today's date    (new period began at 00:30).
    """
    now    = datetime.now(timezone.utc)
    cutoff = now.replace(hour=0, minute=30, second=0, microsecond=0)
    if now < cutoff:
        cutoff -= timedelta(days=1)
    return cutoff.strftime("%Y-%m-%d")


def _daily_halt_path() -> Path:
    return Path(f"halt_{_period_id()}.flag")   # auto-expires at next 00:30 UTC


def _any_halt_active() -> bool:
    return _PERM_HALT.exists() or _daily_halt_path().exists()


def _load() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(state: dict) -> None:
    _STATE_FILE.write_text(json.dumps(state, indent=2))


# ── public API ────────────────────────────────────────────────────────────────

def update_and_check(bot_name: str, equity: float):
    """
    Update account state and evaluate halt conditions.

    Pass current account equity. Daily P&L is computed here against the
    00:30 UTC baseline snapshot — do NOT pass Alpaca's last_equity-based P&L.

    Returns (should_halt: bool, reason: str, daily_pnl: float).
    """
    state     = _load()
    period    = _period_id()
    now_utc   = datetime.now(timezone.utc)

    # ── Period rollover: new 00:30 UTC window → snapshot fresh baseline ───────
    if state.get("_period") != period:
        print(
            f"\n[RISK] *** New period: {period} 00:30 UTC. "
            f"Baseline equity set to ${equity:,.2f} ***\n"
        )
        state = {
            "_period"          : period,
            "_baseline_equity" : equity,
            "_baseline_set_at" : now_utc.isoformat(),
            "account"          : {},
            "bots"             : {},
        }
        _save(state)

    baseline  = state.get("_baseline_equity", equity)
    daily_pnl = equity - baseline

    # Fast path — halt already in effect (compute pnl for display then return)
    if _any_halt_active():
        _print_status(state, daily_pnl, equity, bot_name)
        if _PERM_HALT.exists():
            return True, "PERMANENT HALT — account equity breached the $22,500 drawdown floor", daily_pnl
        return True, "Daily halt active — account lost $1,000 this period; resets at 00:30 UTC", daily_pnl

    # Write latest snapshot
    state["account"] = {"equity": equity, "daily_pnl": daily_pnl}
    state.setdefault("bots", {})[bot_name] = now_utc.isoformat()
    _save(state)

    _print_status(state, daily_pnl, equity, bot_name)

    # ── Drawdown floor — permanent halt ──────────────────────────────────────
    if equity < DRAWDOWN_FLOOR:
        _PERM_HALT.touch()
        return True, (
            f"Account equity ${equity:,.2f} fell below "
            f"${DRAWDOWN_FLOOR:,.0f} drawdown floor — PERMANENT HALT"
        ), daily_pnl

    # ── Daily loss limit — period-scoped halt ─────────────────────────────────
    if daily_pnl <= -DAILY_LOSS_LIMIT:
        _daily_halt_path().touch()
        return True, (
            f"Daily P&L ${daily_pnl:,.2f} hit -${DAILY_LOSS_LIMIT:,.0f} limit — "
            f"ALL BOTS HALTED until 00:30 UTC"
        ), daily_pnl

    return False, "", daily_pnl


def _print_status(state: dict, daily_pnl: float, equity: float, bot_name: str) -> None:
    active_bots = list(state.get("bots", {}).keys())
    baseline    = state.get("_baseline_equity")
    set_at      = state.get("_baseline_set_at", "")[:16].replace("T", " ")
    perm        = _PERM_HALT.exists()
    daily       = _daily_halt_path().exists()

    print(f"\n[RISK] ══════════ ACCOUNT STATUS ({bot_name}) ══════════")
    print(f"[RISK]   Equity      : ${equity:>12,.2f}")
    print(f"[RISK]   Baseline    : ${baseline:>12,.2f}  (set {set_at} UTC)" if baseline else "[RISK]   Baseline    : not yet set")
    print(f"[RISK]   Daily P&L   : {daily_pnl:>+12.2f}  /  limit: -${DAILY_LOSS_LIMIT:,.0f}")
    print(f"[RISK]   Floor       : ${DRAWDOWN_FLOOR:>12,.0f}  ({'BREACHED' if perm else 'ok'})")
    print(f"[RISK]   Active bots : {', '.join(active_bots) if active_bots else 'none'}")
    if perm:
        print("[RISK]   *** PERMANENT HALT — drawdown floor breached ***")
    elif daily:
        print("[RISK]   *** DAILY HALT — resets at 00:30 UTC ***")
    else:
        print("[RISK]   Halt active : no")
    print("[RISK] ══════════════════════════════════════════════\n")
