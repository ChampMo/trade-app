"""Rule 02 as a test, not a promise: only the Risk Engine turns an intent into an order.

The rule is easy to state and easy to erode. A strategy that "just needs to place one order
directly" is how the single auditable path becomes three. This fails the build instead.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "tradeapp"

# Where an OrderRequest may legitimately be built.
ORDER_BUILDERS = {
    "contracts.py",  # defines it
    "risk/engine.py",  # the one door (rule 02)
    "smoke.py",  # Phase 0 proof, DEMO only, documented in CLAUDE.md
}
TRADING_CALLS = {"market_order", "close_position", "modify_sltp"}
# Which modules may reach the broker's trading calls, and which calls each is allowed.
# The kill switch is deliberately narrow: in an emergency it must reach the broker directly rather
# than through layers that may be the thing that is broken, but it may only ever CLOSE.
ALLOWED_BROKER_CALLS = {
    "broker/mt5_bridge.py": TRADING_CALLS,
    "broker/fake.py": TRADING_CALLS,
    "execution.py": TRADING_CALLS,  # the execution layer; everything that trades goes through it
    "risk/killswitch.py": {"close_position"},
}


def _modules():
    for path in sorted(SRC.rglob("*.py")):
        yield path.relative_to(SRC).as_posix(), path


def test_only_the_risk_engine_builds_orders():
    offenders = []
    for rel, path in _modules():
        if rel in ORDER_BUILDERS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "OrderRequest":
                offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, "only the Risk Engine may build an OrderRequest (rule 02):\n" + "\n".join(offenders)


def test_trading_calls_only_happen_where_they_are_allowed():
    offenders = []
    for rel, path in _modules():
        allowed = ALLOWED_BROKER_CALLS.get(rel, set())
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                call = node.func.attr
                if call in TRADING_CALLS and call not in allowed:
                    offenders.append(f"{rel}:{node.lineno} calls {call}")
    assert not offenders, "trading calls belong behind the execution layer (rule 02):\n" + "\n".join(offenders)


def test_the_kill_switch_can_close_but_never_open():
    """An emergency brake that can also open positions is not a brake."""
    text = (SRC / "risk" / "killswitch.py").read_text(encoding="utf-8")
    assert "close_position" in text
    assert "market_order" not in text


def test_the_risk_engine_itself_never_touches_a_broker():
    """It decides and sizes; sending is the execution layer's job (P1-06)."""
    text = (SRC / "risk" / "engine.py").read_text(encoding="utf-8")
    for call in TRADING_CALLS:
        assert call not in text, f"risk engine must not call {call}"


def test_every_order_request_still_demands_a_stop():
    """Rule 03 lives in the dataclass, so no builder anywhere can skip it."""
    import pytest

    from tradeapp.contracts import OrderRequest, Side

    with pytest.raises(ValueError):
        OrderRequest(symbol="EURUSD", side=Side.LONG, volume=0.1, stop_price=0.0, take_price=None, magic=1)
