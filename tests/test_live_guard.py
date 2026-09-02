"""Rule 8: a REAL account is refused unless ALLOW_LIVE. Exercised through the guard, FakeBroker and the MT5 bridge."""

import pytest

from tests.fakes import FakeMT5Module
from tradeapp.broker.fake import FakeBehavior, FakeBroker
from tradeapp.broker.guard import enforce_live_guard
from tradeapp.broker.mt5_bridge import MT5Broker
from tradeapp.contracts import AccountInfo, AccountMode, LiveAccountBlocked


def _acct(mode: AccountMode) -> AccountInfo:
    return AccountInfo(
        login=1, server="s", mode=mode, balance=1, equity=1, currency="USD", leverage=1, algo_trading=True
    )


def test_guard_blocks_real_without_flag():
    with pytest.raises(LiveAccountBlocked):
        enforce_live_guard(_acct(AccountMode.REAL), allow_live=False)


def test_guard_passes_demo_and_flagged_real():
    enforce_live_guard(_acct(AccountMode.DEMO), allow_live=False)
    enforce_live_guard(_acct(AccountMode.CONTEST), allow_live=False)
    enforce_live_guard(_acct(AccountMode.REAL), allow_live=True)


def test_fake_broker_refuses_real():
    b = FakeBroker(behavior=FakeBehavior(mode=AccountMode.REAL))
    with pytest.raises(LiveAccountBlocked):
        b.connect()
    assert b.connected is False


def test_bridge_blocks_real_and_shuts_down():
    mod = FakeMT5Module(trade_mode=2)  # ACCOUNT_TRADE_MODE_REAL
    b = MT5Broker(login=123, password="x", server="XM-Demo", allow_live=False, mt5_module=mod)
    with pytest.raises(LiveAccountBlocked):
        b.connect()
    assert mod.shutdown_calls == 1
    assert b.connected is False


def test_bridge_connects_demo_and_passes_credentials():
    mod = FakeMT5Module(trade_mode=0)  # DEMO
    b = MT5Broker(path=r"C:\MT5\terminal64.exe", login=123, password="pw", server="XM-Demo", mt5_module=mod)
    acct = b.connect()
    assert acct.mode is AccountMode.DEMO and acct.algo_trading is True
    assert mod.init_kwargs["path"].endswith("terminal64.exe")
    assert mod.init_kwargs["login"] == 123 and mod.init_kwargs["password"] == "pw"
    b.disconnect()
    assert mod.shutdown_calls == 1


def test_bridge_allows_real_only_with_flag():
    mod = FakeMT5Module(trade_mode=2)
    b = MT5Broker(login=1, password="x", server="s", allow_live=True, mt5_module=mod)
    assert b.connect().mode is AccountMode.REAL


def test_bridge_reports_initialize_failure():
    mod = FakeMT5Module(trade_mode=0, init_ok=False)
    b = MT5Broker(mt5_module=mod)
    with pytest.raises(Exception, match="initialize failed"):
        b.connect()
