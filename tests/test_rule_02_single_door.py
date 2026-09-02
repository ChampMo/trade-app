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
# Where the broker's trading calls may be reached.
BROKER_CALLERS = {
    "broker/mt5_bridge.py",
    "broker/fake.py",
    "smoke.py",
}
TRADING_CALLS = {"market_order", "close_position", "modify_sltp"}


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


def test_only_the_bridge_and_smoke_call_the_broker_directly():
    offenders = []
    for rel, path in _modules():
        if rel in BROKER_CALLERS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in TRADING_CALLS:
                offenders.append(f"{rel}:{node.lineno} calls {node.func.attr}")
    assert not offenders, "trading calls belong behind the execution layer (rule 02):\n" + "\n".join(offenders)


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
