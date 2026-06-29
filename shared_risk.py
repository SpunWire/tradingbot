"""
Shared risk module — monitors combined P&L across both bots via a JSON state file.
Both processes read/write the same file; the halt flag is a day-scoped sentinel file.
"""

import json
from pathlib import Path
from datetime import datetime, timezone

_STATE_FILE = Path("shared_state.json")

COMBINED_DAILY_LOSS_LIMIT = 1_000.0   # Halt all bots when combined daily loss hits this
DRAWDOWN_FLOOR = 22_500.0              # Permanent per-bot halt if equity drops below this


# ── internal helpers ──────────────────────────────────────────────────────────

def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _halt_path() -> Path:
    return Path(f"halt_{_today()}.flag")


def _load() -> dict:
    try:
        data = json.loads(_STATE_FILE.read_text())
        if data.get("_date") != _today():
            return {"_date": _today()}
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {"_date": _today()}


def _save(state: dict) -> None:
    _STATE_FILE.write_text(json.dumps(state, indent=2))


def _bot_entries(state: dict) -> dict:
    return {k: v for k, v in state.items() if k != "_date" and isinstance(v, dict)}


# ── public API ────────────────────────────────────────────────────────────────

def update_and_check(bot_name: str, daily_pnl: float, equity: float):
    """
    Update this bot's reported state and evaluate all halt conditions.

    Returns (should_halt: bool, reason: str).
    Call at the start of every loop iteration AND before placing any order.
    Prints combined status on every call.
    """
    # Fast path — global daily halt already triggered
    if _halt_path().exists():
        _print_status(_bot_entries(_load()), None)
        return True, "Global daily halt is active — combined loss limit was reached today"

    # Write this bot's current state
    state = _load()
    state[bot_name] = {"pnl": daily_pnl, "equity": equity}
    _save(state)

    bots = _bot_entries(state)
    combined_pnl = sum(v["pnl"] for v in bots.values())

    _print_status(bots, combined_pnl)

    # Per-bot drawdown floor — permanent halt
    if equity < DRAWDOWN_FLOOR:
        reason = (
            f"{bot_name}: equity ${equity:,.2f} is below the "
            f"${DRAWDOWN_FLOOR:,.0f} drawdown floor — PERMANENT HALT"
        )
        return True, reason

    # Combined daily loss limit — day-scoped halt for both bots
    if combined_pnl <= -COMBINED_DAILY_LOSS_LIMIT:
        _halt_path().touch()
        return True, (
            f"Combined daily P&L ${combined_pnl:,.2f} hit "
            f"-${COMBINED_DAILY_LOSS_LIMIT:,.0f} limit — ALL BOTS HALTED for today"
        )

    return False, ""


def _print_status(bots: dict, combined_pnl) -> None:
    print("\n[RISK] ═══════════ COMBINED STATUS ═══════════")
    for name, data in bots.items():
        print(
            f"[RISK]   {name:<14} "
            f"equity=${data['equity']:>10,.2f}   "
            f"daily P&L={data['pnl']:>+9.2f}"
        )
    if combined_pnl is not None:
        print(
            f"[RISK]   {'COMBINED':<14} "
            f"daily P&L={combined_pnl:>+9.2f}   "
            f"limit=-${COMBINED_DAILY_LOSS_LIMIT:,.0f}"
        )
    print(f"[RISK]   Halt active: {'YES *** ' if _halt_path().exists() else 'no'}")
    print("[RISK] ═══════════════════════════════════════\n")
