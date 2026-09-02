"""An MT5 startup failure must name its likely cause; a bare tuple sends the reader to MQL5 docs."""

import pytest

from tests.fakes import FakeMT5Module
from tradeapp.broker.mt5_bridge import MT5Broker, describe_init_error
from tradeapp.contracts import BrokerError


def test_auth_failure_explains_the_account_and_server_mismatch():
    msg = describe_init_error((-6, "Terminal: Authorization failed"))
    assert "code -6" in msg
    assert "server" in msg and "welcome email" in msg


def test_known_codes_carry_an_action():
    assert "MT5_PATH" in describe_init_error((-4, "Terminal not found"))
    assert "Algo Trading" in describe_init_error((-8, "Auto trading disabled"))


def test_unknown_code_still_reports_cleanly():
    assert describe_init_error((-999, "Something else")) == "Something else (code -999)"


def test_malformed_error_is_passed_through():
    assert describe_init_error("boom") == "boom"


def test_connect_surfaces_the_hint():
    mod = FakeMT5Module(trade_mode=0, init_ok=False, last_error=(-6, "Terminal: Authorization failed"))
    with pytest.raises(BrokerError, match="welcome email"):
        MT5Broker(mt5_module=mod).connect()
