"""Broker server time vs UTC (D13, P0-08).

The trap this module exists to close: MetaTrader 5 hands out tick and bar times as a Unix epoch,
but that epoch encodes the BROKER's wall clock, not UTC. Feeding it to `fromtimestamp(..., UTC)`
yields the broker's local time wearing a UTC label. XM and most FX brokers run EET, so that label
is wrong by two or three hours, and the amount changes twice a year at DST. Journal in that time
and every later comparison between backtest and live is silently skewed by hours.

So: the offset is measured against the local clock, recorded next to anything carrying broker time,
and used to convert. Everything here is pure and unit tested; the bridge only calls it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

# Real server offsets are whole or half hours, and never beyond the inhabited timezone range.
OFFSET_GRANULARITY_MIN = 30
MAX_ABS_OFFSET_MIN = 14 * 60
# Local and server clocks drift; allow a little before assuming the tick came from the future.
CLOCK_SKEW_TOLERANCE_MIN = 1.0
# A tick older than this says the market is quiet, so the measurement is a guess, not a reading.
FRESH_TICK_MAX_AGE_S = 300.0


@dataclass(frozen=True)
class ServerTimeOffset:
    """How far the broker's wall clock runs ahead of UTC."""

    minutes: int | None  # None when the reference tick was too old to tell
    tick_age_s: float | None
    confident: bool
    note: str

    @property
    def known(self) -> bool:
        return self.minutes is not None

    def describe(self) -> str:
        if self.minutes is None:
            return f"server offset unknown ({self.note})"
        sign = "+" if self.minutes >= 0 else "-"
        h, m = divmod(abs(self.minutes), 60)
        conf = "measured" if self.confident else "inferred from a stale tick"
        return f"server clock UTC{sign}{h:02d}:{m:02d} ({conf})"


def measure_offset(server_epoch: float, now_utc: datetime | None = None) -> ServerTimeOffset:
    """Derive the server offset from one MT5 timestamp and the local clock.

    `server_epoch` is what MT5 reports for a tick or bar: seconds since 1970 of the broker's
    wall clock. Because a tick is always from the past, the true offset is the smallest allowed
    step at or above the observed difference; that recovers the right answer even when the last
    tick is up to half an hour old, and refuses to guess once it is older than any real timezone.
    """
    now = now_utc or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    server_wall = datetime.fromtimestamp(server_epoch, UTC)  # deliberately mislabelled; see module docstring
    delta_s = (server_wall - now).total_seconds()
    delta_min = delta_s / 60.0

    steps = (delta_min - CLOCK_SKEW_TOLERANCE_MIN) / OFFSET_GRANULARITY_MIN
    candidate = int(math.ceil(steps) * OFFSET_GRANULARITY_MIN)

    if abs(candidate) > MAX_ABS_OFFSET_MIN:
        return ServerTimeOffset(
            minutes=None,
            tick_age_s=None,
            confident=False,
            note=f"reference tick is {abs(delta_min) / 60:.1f} h away from now; market is closed or the clock is wrong",
        )

    age_s = candidate * 60 - delta_s
    confident = age_s <= FRESH_TICK_MAX_AGE_S
    note = "fresh tick" if confident else f"reference tick is {age_s / 60:.1f} min old"
    return ServerTimeOffset(minutes=candidate, tick_age_s=age_s, confident=confident, note=note)


def server_to_utc(server_time: datetime, offset_min: int) -> datetime:
    """Broker wall clock -> real UTC (timezone aware)."""
    naive = server_time.replace(tzinfo=None)
    return (naive - timedelta(minutes=offset_min)).replace(tzinfo=UTC)


def utc_to_server(moment: datetime, offset_min: int) -> datetime:
    """Real UTC -> broker wall clock (naive, because the broker's zone has no name here)."""
    aware = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return (aware.astimezone(UTC) + timedelta(minutes=offset_min)).replace(tzinfo=None)


def epoch_to_server_wall(server_epoch: float) -> datetime:
    """The raw MT5 timestamp read for what it is: the broker's wall clock, naive."""
    return datetime.fromtimestamp(server_epoch, UTC).replace(tzinfo=None)
