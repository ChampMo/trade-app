"""Sizing arithmetic. If this is wrong every limit above it is decorative."""

import pytest

from tradeapp.contracts import Position, Side, SymbolInfo
from tradeapp.risk.sizing import (
    currency_units,
    lots_for_risk,
    money_at_risk_per_lot,
    net_currency_exposure,
    position_risk,
    round_down_to_step,
    split_pair,
)

EURUSD = SymbolInfo(
    symbol="EURUSD",
    digits=5,
    point=0.00001,
    volume_min=0.01,
    volume_step=0.01,
    stops_level_points=0,
    spread_points=10,
    trade_allowed=True,
    tick_size=0.00001,
    tick_value=1.0,
    volume_max=100.0,
    contract_size=100_000.0,
)


def test_money_per_lot_matches_hand_calculation():
    """One lot of EURUSD is 100k EUR, so a 20 pip stop costs $200."""
    assert money_at_risk_per_lot(0.0020, EURUSD) == pytest.approx(200.0)
    assert money_at_risk_per_lot(0.0001, EURUSD) == pytest.approx(10.0)


def test_money_per_lot_needs_broker_tick_data():
    broken = SymbolInfo(**{**EURUSD.__dict__, "tick_value": 0.0})
    with pytest.raises(ValueError, match="tick_size/tick_value"):
        money_at_risk_per_lot(0.0020, broken)
    with pytest.raises(ValueError, match="stop_distance"):
        money_at_risk_per_lot(0.0, EURUSD)


def test_lots_round_down_so_the_budget_is_never_exceeded():
    # $25 of risk over a $200-per-lot stop is 0.125 lots, which must become 0.12 and not 0.13
    lots = lots_for_risk(25.0, 0.0020, EURUSD)
    assert lots == 0.12
    assert lots * money_at_risk_per_lot(0.0020, EURUSD) <= 25.0


def test_lots_are_zero_when_the_budget_is_gone():
    assert lots_for_risk(0.0, 0.0020, EURUSD) == 0.0
    assert lots_for_risk(-5.0, 0.0020, EURUSD) == 0.0


def test_round_down_to_step_is_exact_at_the_boundary():
    assert round_down_to_step(0.29999999, 0.01) == 0.29
    assert round_down_to_step(0.3, 0.01) == 0.3  # floating point must not eat a whole step
    assert round_down_to_step(1.0, 0.1) == 1.0
    assert round_down_to_step(0.009, 0.01) == 0.0


def test_position_risk_uses_the_stop_at_the_broker():
    pos = Position(1, "EURUSD", Side.LONG, 0.5, 1.16000, sl=1.15800, tp=0.0, profit=0.0, magic=1)
    assert position_risk(pos, EURUSD) == pytest.approx(100.0)  # 200 points * $1 * 0.5 lots


def test_position_without_a_stop_is_unbounded_risk():
    pos = Position(1, "EURUSD", Side.LONG, 0.5, 1.16000, sl=0.0, tp=0.0, profit=0.0, magic=1)
    with pytest.raises(ValueError, match="rule 03"):
        position_risk(pos, EURUSD)


def test_split_pair_handles_broker_suffixes():
    assert split_pair("EURUSD") == ("EUR", "USD")
    assert split_pair("EURUSD.raw") == ("EUR", "USD")
    assert split_pair("GBPJPY-ECN") == ("GBP", "JPY")
    assert split_pair("EURUSD_i") == ("EUR", "USD")
    assert split_pair("XAUUSD") == ("XAU", "USD")
    assert split_pair("US30") is None  # not a pair; caller must not pretend to net it


def test_currency_units_express_both_legs():
    assert currency_units("EURUSD", Side.LONG) == {"EUR": 1, "USD": -1}
    assert currency_units("EURUSD", Side.SHORT) == {"EUR": -1, "USD": 1}
    assert currency_units("EURUSD", Side.FLAT) == {}
    assert currency_units("US30", Side.LONG) == {}


def test_exposure_nets_correlated_positions():
    """Long EURUSD and long GBPUSD is two units of short USD, not two unrelated trades."""
    positions = [
        Position(1, "EURUSD", Side.LONG, 0.1, 1.16, 1.15, 0.0, 0.0, 1),
        Position(2, "GBPUSD", Side.LONG, 0.1, 1.35, 1.34, 0.0, 0.0, 2),
    ]
    assert net_currency_exposure(positions) == {"EUR": 1, "GBP": 1, "USD": -2}


def test_exposure_cancels_opposite_sides():
    positions = [
        Position(1, "EURUSD", Side.LONG, 0.1, 1.16, 1.15, 0.0, 0.0, 1),
        Position(2, "EURUSD", Side.SHORT, 0.1, 1.16, 1.17, 0.0, 0.0, 2),
    ]
    assert net_currency_exposure(positions) == {}


def test_exposure_can_include_a_position_not_yet_placed():
    positions = [Position(1, "EURUSD", Side.LONG, 0.1, 1.16, 1.15, 0.0, 0.0, 1)]
    assert net_currency_exposure(positions, currency_units("EURUSD", Side.LONG)) == {"EUR": 2, "USD": -2}
