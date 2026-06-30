"""
Shared risk module — single Alpaca paper account, two bots.

Both bots connect to the same account, so equity/P&L readings are identical.
We store ONE authoritative "account" entry in the state file rather than
summing per-bot values (which would double-count the same account).

Halt conditions:
  - Daily loss limit   : account daily P&L <= -$1,000  →  both bots halt for today
  - Drawdown floor     : account equity    <  $22,500   →  both bots halt permanently
"""

import json
from pathlib import Path
from datetime import datetime, timezone

_STATE_FILE    = Path("shared_state.json")
_PERM_HALT     = Path("halt_permanent.flag")   # drawdown floor breach — never resets

DAILY_LOSS_LIMIT = 1_000.0   # All active bots stop for the day if account loses this much
DRAWDOWN_FLOOR   = 22_500.0  # All active bots stop permanently if account equity falls below this
# Supports any number of bots (spy_bot, crypto_bot, forex_bot, etc.)
# forex_bot registers here but is inactive until its broker functions are wired up


# ── internal helpers ──────────────────────────────────────────────────────────

def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _daily_halt_path() -> Path:
    return Path(f"halt_{_today()}.flag")   # auto-expires next UTC day


def _any_halt_active() -> bool:
    return _PERM_HALT.exists() or _daily_halt_path().exists()


def _load() -> dict:
    try:
        data = json.loads(_STATE_FILE.read_text())
        if data.get("_date") != _today():
            # New UTC day — reset daily state but keep permanent halt flag on disk
            return {"_date": _today(), "account": {}, "bots": {}}
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {"_date": _today(), "account": {}, "bots": {}}


def _save(state: dict) -> None:
    _STATE_FILE.write_text(json.dumps(state, indent=2))


# ── public API ────────────────────────────────────────────────────────────────

def update_and_check(bot_name: str, daily_pnl: float, equity: float):
    """
    Write the latest account snapshot and evaluate halt conditions.

    Both bots call this with the same account's equity/P&L — only the most
    recent write matters since it's one account. Per-bot entries track activity.

    Returns (should_halt: bool, reason: str).
    """
    # Fast path — a halt is already in effect
    if _any_halt_active():
        state = _load()
        _print_status(state, daily_pnl, equity, bot_name)
        if _PERM_HALT.exists():
            return True, "PERMANENT HALT — account equity breached the $22,500 drawdown floor"
        return True, "Daily halt active — account lost $1,000 today; resumes tomorrow"

    # Update shared state with this bot's reading of the single account
    state = _load()
    state["account"] = {"pnl": daily_pnl, "equity": equity}
    state.setdefault("bots", {})[bot_name] = datetime.now(timezone.utc).isoformat()
    _save(state)

    _print_status(state, daily_pnl, equity, bot_name)

    # ── Drawdown floor — permanent halt ──────────────────────────────────────
    if equity < DRAWDOWN_FLOOR:
        _PERM_HALT.touch()
        return True, (
            f"Account equity ${equity:,.2f} fell below the "
            f"${DRAWDOWN_FLOOR:,.0f} drawdown floor — PERMANENT HALT"
        )

    # ── Daily loss limit — day-scoped halt ───────────────────────────────────
    if daily_pnl <= -DAILY_LOSS_LIMIT:
        _daily_halt_path().touch()
        return True, (
            f"Account daily P&L ${daily_pnl:,.2f} hit "
            f"-${DAILY_LOSS_LIMIT:,.0f} limit — BOTH BOTS HALTED for today"
        )

    return False, ""


def _print_status(state: dict, daily_pnl: float, equity: float, bot_name: str) -> None:
    active_bots = list(state.get("bots", {}).keys())
    perm  = _PERM_HALT.exists()
    daily = _daily_halt_path().exists()

    print(f"\n[RISK] ══════════ ACCOUNT STATUS ({bot_name}) ══════════")
    print(f"[RISK]   Equity      : ${equity:>12,.2f}")
    print(f"[RISK]   Daily P&L   : {daily_pnl:>+12.2f}  /  limit: -${DAILY_LOSS_LIMIT:,.0f}")
    print(f"[RISK]   Floor       : ${DRAWDOWN_FLOOR:>12,.0f}  ({'BREACHED' if perm else 'ok'})")
    print(f"[RISK]   Active bots : {', '.join(active_bots) if active_bots else 'none'}")
    if perm:
        print("[RISK]   *** PERMANENT HALT — drawdown floor breached ***")
    elif daily:
        print("[RISK]   *** DAILY HALT — loss limit hit; resets tomorrow ***")
    else:
        print("[RISK]   Halt active : no")
    print("[RISK] ══════════════════════════════════════════════\n")
