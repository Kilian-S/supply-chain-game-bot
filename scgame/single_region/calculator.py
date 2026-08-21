"""Decision engine for the Single-Region Run.

The Single-Region Run controls one factory and one warehouse, both in Calopeia,
whose demand is strongly seasonal with a 365-day period and a peak roughly twice
the annual mean. Factory capacity is fixed once bought and takes 90 days to come
online, so the only lever that can respond to the seasonal peak within the game
horizon is inventory built up ahead of it.

The engine runs one decision cycle per game day. It forecasts demand with
Holt-Winters additive exponential smoothing, measures how far forecast demand
exceeds capacity over the coming half year, selects one of three operating
modes from that comparison, and converts the selected mode into a reorder point
and an order quantity that are written back to the game.

The three operating modes are named Build, Chase, and Drawdown throughout this
project, and each is defined once, here.

Every policy constant in this module is the value that was in force during the
assessed run. They are preserved exactly so that the simulator reproduces the
run that was played rather than an improved variant of it.
"""

import numpy as np
import pandas as pd
from enum import Enum
from scipy import stats
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from ..common.economics import (
    GAME_END_DAY,
    ENDGAME_START_DAY,
    SEASONAL_PERIOD,
    SHIPPING_DAYS_TRUCK,
    SHIPPING_DAYS_MAIL,
)

# --- Shipping method labels -------------------------------------------------

SHIPPING_TRUCK = "TRUCK"
SHIPPING_MAIL = "MAIL"

# --- Policy constants, as played --------------------------------------------

# One full truck. Shipping charges a flat $15,000 per truck for up to 200 drums,
# so any batch that is not a multiple of 200 wastes truck capacity.
STANDARD_ORDER_QUANTITY = 200

# Horizon over which the engine accumulates the shortfall of capacity against
# demand. Half a year reaches the next seasonal peak from any point in the cycle,
# which a shorter horizon would miss entirely during the trough.
FUTURE_DEFICIT_HORIZON_DAYS = 182

# Uplift applied to the accumulated shortfall. Calibrated by repeated simulation
# to absorb forecast error and demand volatility during the peak.
FUTURE_DEFICIT_MULTIPLIER = 1.3

# Window over which mean forecast demand is compared against capacity to choose
# an operating mode.
MODE_LOOKAHEAD_DAYS = 14

# Safety stock settings. The service level is deliberately extreme and is then
# doubled again, because a lost sale costs the full $315 contribution while
# holding a drum costs $0.25 per day, so oversupply is roughly a thousand times
# cheaper per unit than undersupply.
SAFETY_STOCK_SERVICE_LEVEL = 0.999
SAFETY_STOCK_MULTIPLIER = 2.0
DEMAND_STATISTICS_WINDOW_DAYS = 30

# Reorder point held during Drawdown. This sits far above any inventory level
# the warehouse can physically reach, which is precisely the intent: the
# comparison the game makes to decide whether to produce can then never fail,
# so the factory runs continuously at full capacity for the whole of Drawdown.
# Verified by simulation before the run.
DRAWDOWN_UNREACHABLE_REORDER_POINT = 3000

# From this day the engine stops replenishing to a seasonal target and begins
# liquidating, so the reorder point is scaled down towards zero.
ENDGAME_ROP_TAPER_START_DAY = 1400
ENDGAME_ROP_MULTIPLIER = 0.2

# Endgame liquidation. Demand declines linearly to zero between day 1430 and day
# 1460. The engine switches to mail from day 1430 because mail delivers in one
# day against a truck's seven, which is what allows the final orders to be sized
# precisely, and it aims to hold nothing after day 1457.
ENDGAME_MAIL_SWITCH_DAY = ENDGAME_START_DAY
TARGET_ZERO_INVENTORY_DAY = 1457
ENDGAME_SAFETY_BUFFER_DAYS = 2


class OperatingMode(Enum):
    """The three states the Single-Region Run switches between."""

    BUILD = "build"        # Spare capacity and a peak ahead, so accumulate stock
    CHASE = "chase"        # Production tracks demand, inventory held steady
    DRAWDOWN = "drawdown"  # Demand exceeds capacity, so run flat out and deplete


# ---------------------------------------------------------------------------
# Lead time
# ---------------------------------------------------------------------------

def calculate_effective_lead_time(
    order_quantity: int,
    capacity: int,
    shipping_method: str = SHIPPING_TRUCK,
) -> int:
    """Return days from triggering an order to that order reaching the warehouse.

    The factory builds one batch at a time and releases it only when the whole
    batch is complete, so production takes `ceil(order_quantity / capacity)`
    days. Shipping then adds seven days by truck or one day by mail.
    """
    production_days = int(np.ceil(order_quantity / capacity))
    shipping_days = (
        SHIPPING_DAYS_MAIL if shipping_method == SHIPPING_MAIL else SHIPPING_DAYS_TRUCK
    )
    return production_days + shipping_days


# ---------------------------------------------------------------------------
# Forecasting
# ---------------------------------------------------------------------------

def forecast_demand(
    historical_demand: pd.DataFrame,
    current_day: int,
    horizon_days: int,
) -> np.ndarray:
    """Forecast daily demand for the next `horizon_days` days.

    Holt-Winters additive exponential smoothing is fitted to the full demand
    history. The additive form suits Calopeia because the seasonal swing is
    roughly constant in absolute drums rather than proportional to the level.
    Two complete seasonal cycles are required before the seasonal component can
    be identified, and until then the forecast falls back to a 30-day mean.

    The endgame decline is applied on top of the fitted forecast, because the
    decline is a scripted property of the game rather than a pattern present in
    the history the model sees.

    Args:
        historical_demand: Frame with columns `day` and `demand`, from day 1 to
            `current_day`.
        current_day: The day the forecast is made on. Element 0 of the result is
            the forecast for `current_day + 1`.
        horizon_days: Number of days to forecast.

    Returns:
        Array of length `horizon_days`, clipped at zero.
    """
    demand_series = historical_demand["demand"].values

    if len(demand_series) < 2 * SEASONAL_PERIOD:
        window = min(len(demand_series), DEMAND_STATISTICS_WINDOW_DAYS)
        forecast = np.full(horizon_days, demand_series[-window:].mean())
    else:
        model = ExponentialSmoothing(
            demand_series,
            seasonal_periods=SEASONAL_PERIOD,
            trend="add",
            seasonal="add",
            initialization_method="estimated",
        )
        forecast = model.fit(optimized=True).forecast(horizon_days)

    forecast = apply_endgame_decline(forecast, current_day)
    return np.clip(forecast, 0, None)


def apply_endgame_decline(forecast: np.ndarray, current_day: int) -> np.ndarray:
    """Scale forecast demand down linearly to zero across the endgame.

    Demand falls linearly from its day-1430 level to zero at day 1460, so a
    forecast for a day inside that window is scaled by the fraction of the
    window still remaining. Days past the end of the game are set to zero.
    """
    adjusted = forecast.copy()
    decline_period = GAME_END_DAY - ENDGAME_START_DAY

    for index in range(len(forecast)):
        forecast_day = current_day + index + 1

        if forecast_day > GAME_END_DAY:
            adjusted[index] = 0.0
        elif forecast_day > ENDGAME_START_DAY:
            days_remaining = GAME_END_DAY - forecast_day
            adjusted[index] = forecast[index] * (days_remaining / decline_period)

    return adjusted


def calculate_demand_statistics(
    historical_demand: pd.DataFrame,
    window_days: int = DEMAND_STATISTICS_WINDOW_DAYS,
) -> dict:
    """Summarise recent demand over a trailing window.

    A 30-day trailing window is used rather than the whole history because
    safety stock must reflect volatility during the season being entered, and
    Calopeia's dispersion varies substantially between trough and peak.

    Returns a dictionary with keys `mean`, `std`, `min`, `max`, and `cv`.
    """
    demand_series = historical_demand["demand"].values
    window = (
        demand_series[-window_days:]
        if len(demand_series) >= window_days
        else demand_series
    )

    mean_demand = float(np.mean(window))
    std_demand = float(np.std(window, ddof=1)) if len(window) > 1 else 0.0

    return {
        "mean": mean_demand,
        "std": std_demand,
        "min": float(np.min(window)),
        "max": float(np.max(window)),
        "cv": std_demand / mean_demand if mean_demand > 0 else 0.0,
    }


# ---------------------------------------------------------------------------
# Inventory targets
# ---------------------------------------------------------------------------

def calculate_safety_stock(
    demand_std: float,
    lead_time: int,
    service_level: float = SAFETY_STOCK_SERVICE_LEVEL,
    multiplier: float = SAFETY_STOCK_MULTIPLIER,
) -> int:
    """Return the buffer held against demand variation during the lead time.

    This is the textbook expression `z * sigma_d * sqrt(L)`, scaled by a further
    factor of two. The uplift is justified by the cost asymmetry between holding
    and stocking out, which is set out in `scgame.common.economics`.
    """
    z_score = stats.norm.ppf(service_level)
    return int(np.ceil(z_score * demand_std * np.sqrt(lead_time) * multiplier))


def calculate_future_deficit(forecast_demand: np.ndarray, capacity: int) -> int:
    """Return the total drums that demand will exceed capacity by, with a buffer.

    Summing `max(0, demand - capacity)` across the horizon gives the inventory
    that has to exist before the peak begins, because during the peak the
    factory cannot keep up no matter what it does. The result is scaled by
    `FUTURE_DEFICIT_MULTIPLIER` to absorb forecast error.

    Returns zero when capacity covers demand on every day of the horizon.
    """
    daily_deficit = np.maximum(0, forecast_demand - capacity)
    return int(np.ceil(np.sum(daily_deficit) * FUTURE_DEFICIT_MULTIPLIER))


# ---------------------------------------------------------------------------
# Mode selection
# ---------------------------------------------------------------------------

def select_operating_mode(
    current_day: int,
    forecast_demand: np.ndarray,
    capacity: int,
    current_inventory: int,
    future_deficit: int,
    safety_stock: int,
) -> OperatingMode:
    """Choose the operating mode for today.

    Drawdown is entered when mean demand over the next 14 days exceeds daily
    capacity, because from that point the factory is the binding constraint and
    the only remaining question is how fast accumulated stock is consumed.

    Build is entered when capacity is not binding, a shortfall exists somewhere
    in the coming half year, and inventory has not yet reached the level needed
    to cover it.

    Chase is the remaining case, in which production simply tracks demand.
    """
    lookahead = min(MODE_LOOKAHEAD_DAYS, len(forecast_demand))
    if lookahead == 0:
        return OperatingMode.CHASE

    near_term_demand = float(np.mean(forecast_demand[:lookahead]))

    if near_term_demand > capacity:
        return OperatingMode.DRAWDOWN

    target_inventory = future_deficit + safety_stock
    if future_deficit > 0 and current_inventory < target_inventory:
        return OperatingMode.BUILD

    return OperatingMode.CHASE


# ---------------------------------------------------------------------------
# Reorder point
# ---------------------------------------------------------------------------

def calculate_reorder_point(
    mode: OperatingMode,
    forecast_demand: np.ndarray,
    lead_time: int,
    safety_stock: int,
    current_day: int,
    future_deficit: int = 0,
) -> int:
    """Return the inventory level at which the game should trigger production.

    The game starts a batch whenever warehouse inventory plus in-transit
    inventory falls to or below this value, so raising it makes production more
    eager and lowering it makes production stop.

    Chase holds enough to cover demand across the lead time plus safety stock,
    tapering towards zero from day 1400 as liquidation begins. Build raises that
    to the full accumulated shortfall whenever the shortfall is larger, which
    keeps production triggering until the peak is covered. Drawdown pins the
    reorder point above any reachable inventory level, which keeps the factory
    running flat out.
    """
    lead_time_days = min(lead_time, len(forecast_demand))
    lead_time_demand = float(np.sum(forecast_demand[:lead_time_days]))
    standard_rop = lead_time_demand + safety_stock

    if mode is OperatingMode.CHASE:
        if current_day < ENDGAME_ROP_TAPER_START_DAY:
            reorder_point = standard_rop
        else:
            reorder_point = standard_rop * ENDGAME_ROP_MULTIPLIER

    elif mode is OperatingMode.BUILD:
        reorder_point = max(future_deficit, standard_rop)

    elif mode is OperatingMode.DRAWDOWN:
        reorder_point = DRAWDOWN_UNREACHABLE_REORDER_POINT

    else:
        raise ValueError(f"Unknown operating mode: {mode}")

    return max(0, int(np.ceil(reorder_point)))


# ---------------------------------------------------------------------------
# Order quantity and shipping method
# ---------------------------------------------------------------------------

def calculate_order_quantity(
    current_day: int,
    current_inventory: int,
    forecast_demand: np.ndarray,
    capacity: int,
) -> tuple[int, str]:
    """Return the batch size to produce and the method to ship it by.

    Through normal play this is always one full truck of 200 drums, which is the
    largest quantity that incurs a single $15,000 truck charge. From day 1430 the
    engine switches to endgame liquidation.
    """
    if current_day >= ENDGAME_MAIL_SWITCH_DAY:
        return calculate_endgame_order(
            current_day, current_inventory, forecast_demand, capacity
        )

    return (STANDARD_ORDER_QUANTITY, SHIPPING_TRUCK)


def calculate_endgame_order(
    current_day: int,
    current_inventory: int,
    forecast_demand: np.ndarray,
    capacity: int,
) -> tuple[int, str]:
    """Size the final orders so that inventory reaches zero as demand does.

    Any drum still in the warehouse when the game ends was produced at a loss,
    so from day 1430 the engine orders only the shortfall between remaining
    demand and current inventory, plus two days of cover. Mail is used
    throughout, because its one-day delivery makes the arrival date predictable
    enough to aim at, and because the quantities involved no longer fill a truck.

    Orders that could not physically be produced and shipped before the target
    day are truncated to what the remaining days of capacity can deliver.
    """
    days_until_target = TARGET_ZERO_INVENTORY_DAY - current_day
    if days_until_target <= 0:
        return (0, SHIPPING_MAIL)

    remaining_demand = float(
        np.sum(forecast_demand[: min(days_until_target, len(forecast_demand))])
    )

    buffer_end = min(days_until_target + ENDGAME_SAFETY_BUFFER_DAYS, len(forecast_demand))
    safety_buffer = (
        float(np.sum(forecast_demand[days_until_target:buffer_end]))
        if buffer_end > days_until_target
        else 0.0
    )

    inventory_gap = remaining_demand + safety_buffer - current_inventory
    if inventory_gap <= 0:
        return (0, SHIPPING_MAIL)

    order_quantity = int(np.ceil(inventory_gap))

    lead_time = calculate_effective_lead_time(
        order_quantity, capacity, SHIPPING_MAIL
    )
    if lead_time >= days_until_target:
        producible_days = days_until_target - SHIPPING_DAYS_MAIL
        if producible_days <= 0:
            return (0, SHIPPING_MAIL)
        order_quantity = min(order_quantity, int(producible_days * capacity))
        if order_quantity <= 0:
            return (0, SHIPPING_MAIL)

    return (order_quantity, SHIPPING_MAIL)


# ---------------------------------------------------------------------------
# Stockout guard
# ---------------------------------------------------------------------------

def is_stockout_imminent(
    warehouse_inventory: int,
    in_transit_inventory: int,
    forecast_demand: np.ndarray,
    capacity: int,
    order_quantity: int = STANDARD_ORDER_QUANTITY,
) -> bool:
    """Report whether the warehouse runs dry before a truck order could arrive.

    Inventory is projected forward day by day across the truck lead time. Stock
    already in transit is credited on the last day of the truck shipping window
    rather than when it will actually land, because the game does not expose
    arrival dates, and assuming the latest possible arrival is the conservative
    choice.

    When this returns true the caller may switch the outbound shipping method to
    mail, which applies to batches the moment they finish production and so can
    pull a pending delivery forward by six days.
    """
    truck_lead_time = calculate_effective_lead_time(
        order_quantity, capacity, SHIPPING_TRUCK
    )
    horizon = min(truck_lead_time, len(forecast_demand))
    if horizon == 0:
        return False

    projected_inventory = warehouse_inventory
    for day in range(horizon):
        projected_inventory -= forecast_demand[day]
        if day == SHIPPING_DAYS_TRUCK - 1:
            projected_inventory += in_transit_inventory
        if projected_inventory <= 0:
            return True

    return False
