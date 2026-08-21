"""The Single-Region Run decision engine must behave as the coursework describes."""

import numpy as np
import pandas as pd
import pytest

from scgame.single_region import calculator as calc
from scgame.single_region.calculator import OperatingMode


def flat_forecast(value=10.0, days=365) -> np.ndarray:
    """Return a constant forecast, so mode selection depends only on the level."""
    return np.full(days, value)


# --- Lead time -------------------------------------------------------------


def test_lead_time_is_production_time_plus_shipping_time():
    """Producing 200 drums at 50 a day takes 4 days, then 7 days by truck."""
    assert calc.calculate_effective_lead_time(200, 50, calc.SHIPPING_TRUCK) == 11


def test_mail_shortens_the_lead_time_by_six_days():
    """Mail delivers in one day where a truck takes seven."""
    by_truck = calc.calculate_effective_lead_time(200, 50, calc.SHIPPING_TRUCK)
    by_mail = calc.calculate_effective_lead_time(200, 50, calc.SHIPPING_MAIL)
    assert by_truck - by_mail == 6


def test_a_partial_production_day_still_counts_as_a_whole_day():
    """The factory releases a batch only when all of it is finished."""
    assert calc.calculate_effective_lead_time(201, 50, calc.SHIPPING_MAIL) == 5 + 1


# --- Mode selection --------------------------------------------------------


def test_drawdown_is_entered_when_demand_outruns_capacity():
    """Once demand exceeds capacity the factory can no longer keep pace."""
    mode = calc.select_operating_mode(
        current_day=900,
        forecast_demand=flat_forecast(60),
        capacity=50,
        current_inventory=1000,
        future_deficit=5000,
        safety_stock=200,
    )
    assert mode is OperatingMode.DRAWDOWN


def test_build_is_entered_when_a_peak_is_coming_and_stock_is_short():
    """Spare capacity plus a future shortfall means accumulate now."""
    mode = calc.select_operating_mode(
        current_day=900,
        forecast_demand=flat_forecast(10),
        capacity=50,
        current_inventory=100,
        future_deficit=5000,
        safety_stock=200,
    )
    assert mode is OperatingMode.BUILD


def test_chase_is_entered_once_the_target_stock_has_been_reached():
    """With the shortfall already covered there is nothing left to build."""
    mode = calc.select_operating_mode(
        current_day=900,
        forecast_demand=flat_forecast(10),
        capacity=50,
        current_inventory=10_000,
        future_deficit=5000,
        safety_stock=200,
    )
    assert mode is OperatingMode.CHASE


def test_chase_is_entered_when_no_peak_lies_ahead():
    """Without a future shortfall there is no reason to accumulate."""
    mode = calc.select_operating_mode(
        current_day=900,
        forecast_demand=flat_forecast(10),
        capacity=50,
        current_inventory=0,
        future_deficit=0,
        safety_stock=200,
    )
    assert mode is OperatingMode.CHASE


# --- Reorder point ---------------------------------------------------------


def test_drawdown_pins_the_reorder_point_above_any_reachable_stock():
    """Drawdown must keep the factory producing without interruption."""
    reorder_point = calc.calculate_reorder_point(
        mode=OperatingMode.DRAWDOWN,
        forecast_demand=flat_forecast(60),
        lead_time=11,
        safety_stock=300,
        current_day=900,
    )
    assert reorder_point == calc.DRAWDOWN_UNREACHABLE_REORDER_POINT


def test_build_never_falls_below_the_ordinary_reorder_point():
    """Building for a peak must not weaken cover for the coming lead time."""
    forecast = flat_forecast(40)
    lead_time, safety_stock = 11, 300
    standard = np.sum(forecast[:lead_time]) + safety_stock

    reorder_point = calc.calculate_reorder_point(
        mode=OperatingMode.BUILD,
        forecast_demand=forecast,
        lead_time=lead_time,
        safety_stock=safety_stock,
        current_day=900,
        future_deficit=10,
    )
    assert reorder_point >= standard


def test_build_raises_the_reorder_point_to_the_size_of_the_coming_shortfall():
    """A large shortfall keeps production triggering until it is covered."""
    reorder_point = calc.calculate_reorder_point(
        mode=OperatingMode.BUILD,
        forecast_demand=flat_forecast(10),
        lead_time=11,
        safety_stock=100,
        current_day=900,
        future_deficit=8000,
    )
    assert reorder_point == 8000


def test_the_reorder_point_is_tapered_once_liquidation_begins():
    """From day 1400 the aim shifts from holding stock to clearing it."""
    forecast = flat_forecast(40)
    before = calc.calculate_reorder_point(
        OperatingMode.CHASE, forecast, 11, 300, current_day=1399
    )
    after = calc.calculate_reorder_point(
        OperatingMode.CHASE, forecast, 11, 300, current_day=1401
    )
    assert after < before


def test_the_reorder_point_is_never_negative():
    """A negative reorder point is meaningless to the game."""
    reorder_point = calc.calculate_reorder_point(
        OperatingMode.CHASE, np.zeros(365), 11, 0, current_day=1459
    )
    assert reorder_point >= 0


# --- Endgame ---------------------------------------------------------------


def test_forecast_demand_reaches_zero_at_the_end_of_the_game():
    """Demand declines linearly from day 1430 and is zero by day 1460."""
    forecast = calc.apply_endgame_decline(np.full(40, 60.0), current_day=1425)

    assert forecast[0] == pytest.approx(60.0)      # day 1426, before the decline
    assert forecast[34] == pytest.approx(0.0)      # day 1460, the last day
    assert forecast[35] == pytest.approx(0.0)      # day 1461, past the end


def test_the_endgame_decline_is_monotonic():
    """Demand must fall steadily rather than jump about."""
    forecast = calc.apply_endgame_decline(np.full(40, 60.0), current_day=1429)
    within_decline = forecast[1:31]
    assert all(
        earlier >= later
        for earlier, later in zip(within_decline, within_decline[1:])
    )


def test_no_order_is_placed_once_the_liquidation_target_has_passed():
    """After day 1457 anything produced could not be sold."""
    quantity, method = calc.calculate_endgame_order(
        current_day=calc.TARGET_ZERO_INVENTORY_DAY,
        current_inventory=0,
        forecast_demand=flat_forecast(5),
        capacity=50,
    )
    assert quantity == 0
    assert method == calc.SHIPPING_MAIL


def test_no_order_is_placed_when_stock_already_covers_remaining_demand():
    """Holding enough already means every further drum is a loss."""
    quantity, _ = calc.calculate_endgame_order(
        current_day=1440,
        current_inventory=100_000,
        forecast_demand=flat_forecast(5),
        capacity=50,
    )
    assert quantity == 0


def test_the_endgame_order_is_capped_by_what_can_still_be_produced_in_time():
    """An order that could not arrive before the target day is trimmed."""
    days_left = 3
    capacity = 10
    quantity, _ = calc.calculate_endgame_order(
        current_day=calc.TARGET_ZERO_INVENTORY_DAY - days_left,
        current_inventory=0,
        forecast_demand=flat_forecast(1000),
        capacity=capacity,
    )
    assert quantity <= (days_left - calc.SHIPPING_DAYS_MAIL) * capacity


def test_normal_play_orders_exactly_one_full_truck_by_truck():
    """Any batch that is not a multiple of 200 wastes paid truck capacity."""
    quantity, method = calc.calculate_order_quantity(
        current_day=900,
        current_inventory=500,
        forecast_demand=flat_forecast(40),
        capacity=50,
    )
    assert quantity == calc.STANDARD_ORDER_QUANTITY
    assert method == calc.SHIPPING_TRUCK


# --- Safety stock and shortfall -------------------------------------------


def test_safety_stock_grows_with_volatility_and_with_lead_time():
    """Both a noisier market and a slower supply line need a larger buffer."""
    base = calc.calculate_safety_stock(demand_std=10, lead_time=11)
    noisier = calc.calculate_safety_stock(demand_std=20, lead_time=11)
    slower = calc.calculate_safety_stock(demand_std=10, lead_time=22)
    assert noisier > base
    assert slower > base


def test_a_perfectly_steady_market_needs_no_safety_stock():
    """With no variation there is nothing for the buffer to absorb."""
    assert calc.calculate_safety_stock(demand_std=0, lead_time=11) == 0


def test_no_shortfall_is_reported_when_capacity_covers_every_day():
    """Capacity above demand throughout means nothing has to be pre-built."""
    assert calc.calculate_future_deficit(flat_forecast(10), capacity=50) == 0


def test_the_shortfall_includes_the_calibrated_safety_margin():
    """The raw shortfall is scaled up to absorb forecast error."""
    forecast = np.full(10, 60.0)
    raw = 10 * (60 - 50)
    deficit = calc.calculate_future_deficit(forecast, capacity=50)
    assert deficit == pytest.approx(raw * calc.FUTURE_DEFICIT_MULTIPLIER, abs=1)


# --- Stockout guard --------------------------------------------------------


def test_a_stockout_is_reported_when_stock_runs_out_before_a_truck_could_arrive():
    """This is the signal that justifies paying the mail premium."""
    assert calc.is_stockout_imminent(
        warehouse_inventory=5,
        in_transit_inventory=0,
        forecast_demand=flat_forecast(50),
        capacity=50,
    )


def test_no_stockout_is_reported_when_stock_comfortably_covers_the_lead_time():
    """Ample stock must not trigger unnecessary mail shipping."""
    assert not calc.is_stockout_imminent(
        warehouse_inventory=100_000,
        in_transit_inventory=0,
        forecast_demand=flat_forecast(50),
        capacity=50,
    )


# --- Forecasting -----------------------------------------------------------


def test_a_short_history_falls_back_to_a_recent_average():
    """Holt-Winters needs two full seasons before its season term means anything."""
    history = pd.DataFrame({"day": range(1, 101), "demand": [20.0] * 100})
    forecast = calc.forecast_demand(history, current_day=100, horizon_days=30)

    assert len(forecast) == 30
    assert forecast == pytest.approx(np.full(30, 20.0))


def test_forecasts_are_never_negative():
    """Negative demand is meaningless and would corrupt every downstream sum."""
    history = pd.DataFrame({"day": range(1, 101), "demand": [0.0] * 100})
    forecast = calc.forecast_demand(history, current_day=100, horizon_days=30)
    assert (forecast >= 0).all()
