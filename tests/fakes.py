"""Shared stand-in for the MetaTrader5 package, so tests never need the real terminal."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace


class FakeMT5Module:
    """Minimal surface of the MetaTrader5 package used by MT5Broker.connect()."""

    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2

    def __init__(
        self,
        trade_mode: int,
        init_ok: bool = True,
        server_offset_min: int = 180,
        tick: bool = True,
        last_error: tuple[int, str] = (-1, "fake error"),
    ):
        self._trade_mode = trade_mode
        self._init_ok = init_ok
        self._server_offset_min = server_offset_min
        self._has_tick = tick
        self._last_error = last_error
        self.shutdown_calls = 0
        self.init_kwargs = None

    def initialize(self, **kwargs):
        self.init_kwargs = kwargs
        return self._init_ok

    def shutdown(self):
        self.shutdown_calls += 1

    def last_error(self):
        return self._last_error

    def account_info(self):
        return SimpleNamespace(
            login=123,
            server="XM-Demo",
            trade_mode=self._trade_mode,
            balance=10.0,
            equity=10.0,
            currency="USD",
            leverage=500,
        )

    def terminal_info(self):
        return SimpleNamespace(trade_allowed=True, connected=True)

    def symbol_info_tick(self, symbol):
        if not self._has_tick:
            return None
        # what MT5 reports: the broker's wall clock as an epoch
        now = datetime.now(UTC) + timedelta(minutes=self._server_offset_min)
        return SimpleNamespace(bid=1.1, ask=1.1, time=int(now.timestamp()))

    def symbol_info(self, symbol):
        return SimpleNamespace(
            digits=5,
            point=1e-5,
            volume_min=0.01,
            volume_step=0.01,
            trade_stops_level=0,
            spread=1,
            trade_mode=4,
            visible=True,
            filling_mode=1,
        )
