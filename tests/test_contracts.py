import pytest

from tradeapp.contracts import Intent, OrderRequest, Side


def test_order_request_requires_stop():
    with pytest.raises(ValueError, match="rule 03"):
        OrderRequest(symbol="EURUSD", side=Side.LONG, volume=0.01, stop_price=0.0, take_price=None, magic=1)


def test_order_request_rejects_flat_and_zero_volume():
    with pytest.raises(ValueError):
        OrderRequest(symbol="EURUSD", side=Side.FLAT, volume=0.01, stop_price=1.0, take_price=None, magic=1)
    with pytest.raises(ValueError):
        OrderRequest(symbol="EURUSD", side=Side.LONG, volume=0.0, stop_price=1.0, take_price=None, magic=1)


def test_intent_requires_stop_and_bounded_confidence():
    with pytest.raises(ValueError, match="rule 03"):
        Intent(symbol="EURUSD", side=Side.SHORT, confidence=0.5, stop_price=0.0, take_price=None, reason="x")
    with pytest.raises(ValueError):
        Intent(symbol="EURUSD", side=Side.LONG, confidence=1.5, stop_price=1.0, take_price=None, reason="x")
    flat = Intent(symbol="EURUSD", side=Side.FLAT, confidence=0.0, stop_price=0.0, take_price=None, reason="exit")
    assert flat.side is Side.FLAT
