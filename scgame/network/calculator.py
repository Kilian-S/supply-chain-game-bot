# Handles optimisation and calculation of new parameter settings.
# "Always On" strategy: factory runs at 100% capacity until shutdown trigger.

import numpy as np
import pandas as pd
from enum import Enum

from .config import (
    NETWORK,
    ShippingMethod,
    GAME_END_DAY,
)


# ============================================================
# STRATEGY CONSTANTS - "Always On"
# ============================================================

# Dynamic ROP: set at current inventory + this buffer to always trigger production
ALWAYS_ON_ROP_BUFFER = 1000

# Shutdown logic: stop when inventory covers remaining demand
SHUTDOWN_SAFETY_DAYS = 0  # No safety buffer - we have accurate forecasts

# Calopeia system warehouse allocation
TYRAN_FLOOR = 400  # Minimum inventory floor for Tyran warehouse

# Fardo warehouse capacity cap
FARDO_WAREHOUSE_CAP = 400  # Don't order if Fardo pipeline >= this

# Endgame constants
ENDGAME_START_DAY = 1430
GAME_END_DAY_DEMAND = 1460  # Demand reaches 0 at this day
TARGET_ZERO_INVENTORY_DAY = 1450  # Aim to deplete inventory by this day (accept stockouts after)
HARD_SHUTDOWN_DAY = 1440  # All factories stop production on this day, no exceptions

# Order quantity
STANDARD_ORDER_QUANTITY = 200  # One full truck (minimum batch)

# Tiered batch size thresholds
BATCH_SIZE_TIER_1_THRESHOLD = 1000  # Above this inventory, use 400
BATCH_SIZE_TIER_2_THRESHOLD = 4000  # Above this inventory, use 600
BATCH_SIZE_TIER_1 = 400  # 2 trucks
BATCH_SIZE_TIER_2 = 600  # 3 trucks


def calculate_batch_size(current_inventory: int, in_transit: int = 0) -> int:
    """
    Calculate dynamic batch size based on inventory level.

    Every batch carries a flat $2,000 order charge regardless of its size, so
    a larger batch spreads that charge over more drums. Batch size is stepped up
    with the size of the pipeline, because a large pipeline means the warehouse
    can absorb a bigger delivery without risking the stock running down while
    the longer batch is still in production.

    - pipeline below 1,000 drums: 200 drums, one truck
    - pipeline 1,000 to 3,999 drums: 400 drums, two trucks
    - pipeline 4,000 drums or more: 600 drums, three trucks

    Args:
        current_inventory: Current warehouse inventory.
        in_transit: Inventory in transit (optional, for pipeline consideration).

    Returns:
        Batch size in drums (multiple of 200).
    """
    pipeline = current_inventory + in_transit

    if pipeline >= BATCH_SIZE_TIER_2_THRESHOLD:
        return BATCH_SIZE_TIER_2
    elif pipeline >= BATCH_SIZE_TIER_1_THRESHOLD:
        return BATCH_SIZE_TIER_1
    else:
        return STANDARD_ORDER_QUANTITY

# Shipping
SHIPPING_TRUCK = 'TRUCK'
SHIPPING_MAIL = 'MAIL'
SHIPPING_TIME_TRUCK = 7  # days
SHIPPING_TIME_MAIL = 1   # day

# Forecasting
SEASONAL_PERIOD = 365  # Calopeia seasonal cycle


# ============================================================
# OPERATING MODE
# ============================================================

class OperatingMode(Enum):
    """Operating modes for inventory management strategy."""
    BUILD = "build"       # Factory running at 100% (default state)
    CHASE = "chase"       # Transitional (for compatibility)
    DRAWDOWN = "drawdown" # Factory shutdown, living off inventory


# ============================================================
# KNOWN DEMAND - Perfect forecast for Calopeia
# ============================================================

_KNOWN_CALOPEIA_DEMAND = None


def _load_known_calopeia_demand() -> pd.DataFrame:
    """Load known Calopeia demand from file (cached)."""
    global _KNOWN_CALOPEIA_DEMAND
    if _KNOWN_CALOPEIA_DEMAND is None:
        from pathlib import Path
        file_path = Path(__file__).parent / "calopeia_four_year_demand.xlsx"
        if file_path.exists():
            _KNOWN_CALOPEIA_DEMAND = pd.read_excel(file_path)
        else:
            _KNOWN_CALOPEIA_DEMAND = pd.DataFrame()
    return _KNOWN_CALOPEIA_DEMAND


def _forecast_known_calopeia(current_day: int, horizon_days: int) -> np.ndarray:
    """
    Return known future demand for Calopeia (perfect forecast).
    """
    known_demand = _load_known_calopeia_demand()

    if known_demand.empty:
        return np.zeros(horizon_days)

    forecast = np.zeros(horizon_days)
    for i in range(horizon_days):
        day = current_day + 1 + i
        if day <= GAME_END_DAY:
            row = known_demand[known_demand['day'] == day]
            if not row.empty:
                forecast[i] = row['Calopeia'].iloc[0]

    return forecast


# ============================================================
# DEMAND FORECASTING
# ============================================================

# Sorange linear formula constants (from day 730)
# OLS regression on actual demand data (day 670-1200): y = 0.165*(day-730) + 14.2
SORANGE_SLOPE = 0.165
SORANGE_INTERCEPT = 14.2
SORANGE_START_DAY = 730

# Entworpe fixed daily demand (conservative estimate, actual avg 11-15)
ENTWORPE_DAILY_DEMAND = 15

# Moving average start day for Fardo/Tyran
MOVING_AVG_START_DAY = 670


def forecast_demand(
    region: str,
    historical_demand: pd.DataFrame,
    current_day: int,
    horizon_days: int,
    column: str = None
) -> np.ndarray:
    """
    Forecasts future demand for a specific region.

    Each region gets the simplest method that fits its behaviour, because in
    this scenario the noisier regions punish elaborate forecasting.

    - Calopeia: read from the recorded series, which is exact. The Calopeia
      demand in this run repeated the series already seen in the Single-Region
      Run, so its future was known rather than predicted.
    - Sorange: the linear fit in `_forecast_sorange_formula`, because its long
      run average grows steadily until day 1430.
    - Tyran and Fardo: a moving average from day 670 onwards, because both are
      stable with no trend or season.
    - Entworpe: a flat daily rate, because its orders arrive as occasional
      250-drum blocks that no daily model can time.

    The endgame decline is applied to every region except Calopeia, whose
    recorded series already contains it.
    """
    if region == "Calopeia":
        # Perfect forecast - already includes endgame decline in the data
        forecast = _forecast_known_calopeia(current_day, horizon_days)
        return np.clip(forecast, 0, None)

    elif region == "Sorange":
        forecast = _forecast_sorange_formula(current_day, horizon_days)

    elif region in ("Tyran", "Fardo"):
        forecast = _forecast_moving_average(historical_demand, current_day, horizon_days, column)

    elif region == "Entworpe":
        forecast = np.full(horizon_days, ENTWORPE_DAILY_DEMAND)

    else:
        raise ValueError(f"Unknown region: {region}")

    # Apply endgame linear decline (day 1430-1460) for non-Calopeia regions
    forecast = _apply_endgame_decline(forecast, current_day, horizon_days)

    return np.clip(forecast, 0, None)


def _forecast_sorange_formula(
    current_day: int,
    horizon_days: int
) -> np.ndarray:
    """
    Forecast Sorange demand from its fitted growth line.

    Ordinary least squares on days 670 to 1200 gives
    `demand = 0.165 * (day - 730) + 14.2`, which is the line used here.

    Args:
        current_day: Current simulation day.
        horizon_days: Number of days to forecast.

    Returns:
        np.ndarray of forecasted demand values.
    """
    forecast = np.zeros(horizon_days)

    for i in range(horizon_days):
        forecast_day = current_day + 1 + i
        forecast[i] = SORANGE_SLOPE * (forecast_day - SORANGE_START_DAY) + SORANGE_INTERCEPT

    return forecast


def _forecast_moving_average(
    historical_demand: pd.DataFrame,
    current_day: int,
    horizon_days: int,
    column: str = None
) -> np.ndarray:
    """
    Moving average forecast using data from day 670 to current_day.

    Used for Tyran and Fardo (relatively stable demand).

    Args:
        historical_demand: DataFrame with demand data.
        current_day: Current simulation day.
        horizon_days: Number of days to forecast.
        column: Explicit column name for demand data.

    Returns:
        np.ndarray of forecasted demand values (constant).
    """
    demand_series = _extract_demand_series(historical_demand, column)

    if len(demand_series) == 0:
        return np.zeros(horizon_days)

    # Get data from day 670 onwards
    # historical_demand index corresponds to days, starting from day 1
    start_index = max(0, MOVING_AVG_START_DAY - 1)  # -1 because 0-indexed

    if start_index >= len(demand_series):
        # Not enough data yet, use all available
        window_data = demand_series
    else:
        window_data = demand_series[start_index:]

    if len(window_data) == 0:
        return np.zeros(horizon_days)

    avg_demand = np.mean(window_data)
    return np.full(horizon_days, avg_demand)


def _extract_demand_series(historical_demand: pd.DataFrame, column: str = None) -> np.ndarray:
    """Extracts demand values from DataFrame."""
    if column is not None:
        if column in historical_demand.columns:
            return historical_demand[column].values
        else:
            raise ValueError(f"Column '{column}' not found in DataFrame")

    if 'demand' in historical_demand.columns:
        return historical_demand['demand'].values
    else:
        non_day_cols = [c for c in historical_demand.columns if c.lower() != 'day']
        if non_day_cols:
            return historical_demand[non_day_cols[0]].values
        else:
            raise ValueError("Cannot find demand column in DataFrame")


def _apply_endgame_decline(
    forecast: np.ndarray,
    current_day: int,
    horizon_days: int
) -> np.ndarray:
    """
    Applies linear decline to forecast for endgame period (day 1430-1460).
    Demand drops linearly from its day-1430 level to 0 at day 1460.
    """
    adjusted_forecast = forecast.copy()

    for i in range(horizon_days):
        forecast_day = current_day + i + 1

        if forecast_day > GAME_END_DAY_DEMAND:
            adjusted_forecast[i] = 0
        elif forecast_day > ENDGAME_START_DAY:
            days_remaining = GAME_END_DAY_DEMAND - forecast_day
            decline_period = GAME_END_DAY_DEMAND - ENDGAME_START_DAY
            multiplier = days_remaining / decline_period
            adjusted_forecast[i] = forecast[i] * multiplier

    return adjusted_forecast


# ============================================================
# AGGREGATE DEMAND FOR WAREHOUSES
# ============================================================

def forecast_warehouse_demand(
    warehouse: str,
    regional_historical_demand: dict,
    current_day: int,
    horizon_days: int
) -> np.ndarray:
    """
    Forecasts aggregate demand for a warehouse from all regions it serves.

    Includes Entworpe demand (fixed 15/day with endgame decline) for Calopeia_WH.
    """
    served_regions = NETWORK.get_regions_served_by(warehouse)
    aggregate_forecast = np.zeros(horizon_days)

    for region in served_regions:
        if region == "Entworpe":
            # Entworpe has fixed demand - include it in forecast
            entworpe_forecast = forecast_demand(
                region="Entworpe",
                historical_demand=pd.DataFrame(),  # Not used for Entworpe
                current_day=current_day,
                horizon_days=horizon_days
            )
            aggregate_forecast += entworpe_forecast
        elif region in regional_historical_demand:
            region_forecast = forecast_demand(
                region=region,
                historical_demand=regional_historical_demand[region],
                current_day=current_day,
                horizon_days=horizon_days
            )
            aggregate_forecast += region_forecast

    return aggregate_forecast


# ============================================================
# REMAINING DEMAND CALCULATION (for shutdown logic)
# ============================================================

def calculate_remaining_demand(
    current_day: int,
    forecast_demand: np.ndarray
) -> float:
    """
    Calculates total remaining demand from current day to target zero inventory day.

    Uses TARGET_ZERO_INVENTORY_DAY (not GAME_END_DAY_DEMAND) to allow
    intentional stockouts in the final days.

    Args:
        current_day: Current simulation day.
        forecast_demand: Array of daily demand forecasts (should cover until game end).

    Returns:
        Total remaining demand in drums.
    """
    days_until_target = TARGET_ZERO_INVENTORY_DAY - current_day
    if days_until_target <= 0:
        return 0

    horizon = min(days_until_target, len(forecast_demand))
    return np.sum(forecast_demand[:horizon])


def calculate_remaining_demand_for_warehouse(
    warehouse: str,
    regional_historical_demand: dict,
    current_day: int
) -> float:
    """
    Calculates total remaining demand for a warehouse until target zero inventory day.

    Uses TARGET_ZERO_INVENTORY_DAY to allow intentional stockouts in final days.
    """
    days_until_target = TARGET_ZERO_INVENTORY_DAY - current_day
    if days_until_target <= 0:
        return 0

    forecast = forecast_warehouse_demand(
        warehouse=warehouse,
        regional_historical_demand=regional_historical_demand,
        current_day=current_day,
        horizon_days=days_until_target
    )

    return np.sum(forecast)


# ============================================================
# SHUTDOWN LOGIC - "Always On" Strategy Core
# ============================================================

def should_shutdown(
    current_inventory: int,
    in_transit: int,
    remaining_demand: float,
    current_demand_rate: float
) -> bool:
    """
    Determines if factory should shut down based on inventory position.

    Production stops once the pipeline already covers every drum that will be
    demanded between now and the target liquidation day. Any drum produced after
    that point cannot be sold and so is pure loss.

    `SHUTDOWN_SAFETY_DAYS` is zero, meaning no cushion is added. That is
    deliberate: the demand figures driving this comparison are exact for
    Calopeia and close to exact elsewhere by the time shutdown becomes
    relevant, so a cushion would only leave unsold stock behind.

    Args:
        current_inventory: Inventory in warehouse.
        in_transit: Inventory in transit (truck + mail).
        remaining_demand: Total demand from now until game end.
        current_demand_rate: Current daily demand rate.

    Returns:
        True if factory should shut down (enough inventory to coast).
    """
    inventory_pipeline = current_inventory + in_transit
    safety_buffer = SHUTDOWN_SAFETY_DAYS * current_demand_rate

    return inventory_pipeline >= (remaining_demand + safety_buffer)


def calculate_current_demand_rate(
    forecast_demand: np.ndarray,
    window_days: int = 7
) -> float:
    """
    Calculates current demand rate (average of near-term forecast).

    Args:
        forecast_demand: Array of daily demand forecasts.
        window_days: Number of days to average.

    Returns:
        Average daily demand rate.
    """
    if len(forecast_demand) == 0:
        return 0

    window = min(window_days, len(forecast_demand))
    return np.mean(forecast_demand[:window])


# ============================================================
# REORDER POINT - "Always On" Strategy
# ============================================================

def calculate_always_on_rop(pipeline: int) -> int:
    """
    Calculates dynamic ROP for "Always On" strategy.

    ROP = pipeline + 1000, ensuring factory is always triggered.

    The game triggers production when: inventory + in_transit <= ROP
    So we must set ROP above the current pipeline to guarantee production.

    Args:
        pipeline: Current inventory + in-transit (what game compares against ROP).

    Returns:
        Dynamic ROP value.
    """
    return pipeline + ALWAYS_ON_ROP_BUFFER


def calculate_reorder_point(
    warehouse: str,
    current_inventory: int,
    in_transit: int,
    remaining_demand: float,
    current_demand_rate: float,
    current_day: int
) -> int:
    """
    Calculates ROP using "Always On" strategy.

    - Before shutdown: Returns inventory + 1000 (factory always runs)
    - After shutdown trigger: Returns 0 (factory stops)

    Args:
        warehouse: Warehouse name.
        current_inventory: Inventory in warehouse.
        in_transit: Inventory in transit.
        remaining_demand: Total demand from now until game end.
        current_demand_rate: Current daily demand rate.
        current_day: Current simulation day.

    Returns:
        Reorder point (dynamic ROP or 0).
    """
    # Check if we should shut down
    if should_shutdown(current_inventory, in_transit, remaining_demand, current_demand_rate):
        return 0

    # Otherwise, keep factory running at 100%
    pipeline = current_inventory + in_transit
    return calculate_always_on_rop(pipeline)


def select_operating_mode(
    current_inventory: int,
    in_transit: int,
    remaining_demand: float,
    current_demand_rate: float
) -> OperatingMode:
    """
    Selects operating mode based on shutdown status.

    - DRAWDOWN: Shutdown triggered, living off inventory
    - BUILD: Factory running at 100% (normal state)
    """
    if should_shutdown(current_inventory, in_transit, remaining_demand, current_demand_rate):
        return OperatingMode.DRAWDOWN

    return OperatingMode.BUILD


# ============================================================
# CALOPEIA SYSTEM - Multi-warehouse allocation
# ============================================================

def calculate_calopeia_system_allocation(
    calopeia_wh_inventory: int,
    calopeia_wh_in_transit: int,
    tyran_wh_inventory: int,
    tyran_wh_in_transit: int,
    factory_capacity: int,
    regional_historical_demand: dict,
    current_day: int
) -> dict:
    """
    Calculates allocation for Calopeia system (factory serves Calopeia_WH and Tyran_WH).

    One factory feeds two warehouses, so its output has to be split.

    Tyran is topped up to a floor of `TYRAN_FLOOR` drums and no further, because
    its demand is stable and small. Everything the factory can produce beyond
    that goes to Calopeia, whose seasonal peak is the only place a large buffer
    earns its keep. Priority is handed to whichever warehouse needs it more, so
    Tyran outranks Calopeia only while it sits below its floor.

    Shutdown is judged across both warehouses together, because they share the
    factory that would have to be stopped.

    Args:
        calopeia_wh_inventory: Inventory in Calopeia warehouse.
        calopeia_wh_in_transit: In-transit to Calopeia warehouse.
        tyran_wh_inventory: Inventory in Tyran warehouse.
        tyran_wh_in_transit: In-transit to Tyran warehouse.
        factory_capacity: Daily factory capacity.
        regional_historical_demand: Dict mapping region to demand DataFrame.
        current_day: Current simulation day.

    Returns:
        Dict with keys: 'calopeia_rop', 'calopeia_qty', 'tyran_rop', 'tyran_qty',
                       'calopeia_mode', 'tyran_mode'
    """
    # Hard shutdown: no production after HARD_SHUTDOWN_DAY
    # Set qty=0 to guarantee no production (game rule: order_quantity must be > 0)
    if current_day >= HARD_SHUTDOWN_DAY:
        return {
            'calopeia_rop': 0,
            'calopeia_qty': 0,
            'calopeia_mode': OperatingMode.DRAWDOWN,
            'calopeia_priority': 5,
            'tyran_rop': 0,
            'tyran_qty': 0,
            'tyran_mode': OperatingMode.DRAWDOWN,
            'tyran_priority': 5,
        }

    # Calculate remaining demand for each warehouse
    calopeia_remaining = calculate_remaining_demand_for_warehouse(
        "Calopeia_WH", regional_historical_demand, current_day
    )
    tyran_remaining = calculate_remaining_demand_for_warehouse(
        "Tyran_WH", regional_historical_demand, current_day
    )

    # Calculate current demand rates
    calopeia_forecast = forecast_warehouse_demand(
        "Calopeia_WH", regional_historical_demand, current_day, 30
    )
    tyran_forecast = forecast_warehouse_demand(
        "Tyran_WH", regional_historical_demand, current_day, 30
    )

    calopeia_rate = calculate_current_demand_rate(calopeia_forecast)
    tyran_rate = calculate_current_demand_rate(tyran_forecast)

    # Tyran allocation: maintain 300 floor
    tyran_pipeline = tyran_wh_inventory + tyran_wh_in_transit
    tyran_needs_floor = tyran_pipeline < TYRAN_FLOOR

    # Check system-wide shutdown (always considers total demand)
    total_inventory = calopeia_wh_inventory + tyran_wh_inventory
    total_in_transit = calopeia_wh_in_transit + tyran_wh_in_transit
    total_remaining = calopeia_remaining + tyran_remaining
    total_rate = calopeia_rate + tyran_rate

    system_shutdown = should_shutdown(
        total_inventory, total_in_transit, total_remaining, total_rate
    )

    result = {}

    # Calculate dynamic batch sizes based on inventory levels
    calopeia_batch = calculate_batch_size(calopeia_wh_inventory, calopeia_wh_in_transit)
    tyran_batch = calculate_batch_size(tyran_wh_inventory, tyran_wh_in_transit)

    if system_shutdown:
        # System shutdown - stop all production
        # Set qty=0 to guarantee no production (game rule: order_quantity must be > 0)
        result['calopeia_rop'] = 0
        result['calopeia_qty'] = 0
        result['calopeia_mode'] = OperatingMode.DRAWDOWN
        result['calopeia_priority'] = 5

        result['tyran_rop'] = 0
        result['tyran_qty'] = 0
        result['tyran_mode'] = OperatingMode.DRAWDOWN
        result['tyran_priority'] = 5

    else:
        # Normal operation - Always On
        # Tyran: only fill to floor if below
        if tyran_needs_floor:
            # Tyran needs replenishment up to floor - HIGHEST PRIORITY
            result['tyran_rop'] = TYRAN_FLOOR
            result['tyran_qty'] = tyran_batch
            result['tyran_mode'] = OperatingMode.BUILD
            result['tyran_priority'] = 5  # Highest - needs floor
            result['calopeia_priority'] = 4  # Lower while Tyran fills
        else:
            # Tyran at floor - minimal production
            result['tyran_rop'] = TYRAN_FLOOR  # Maintain floor
            result['tyran_qty'] = tyran_batch
            result['tyran_mode'] = OperatingMode.BUILD
            result['tyran_priority'] = 4  # Lower - at floor
            result['calopeia_priority'] = 5  # Highest - gets all excess

        # Calopeia: gets everything else (dynamic ROP = pipeline + 1000)
        calopeia_pipeline = calopeia_wh_inventory + calopeia_wh_in_transit
        result['calopeia_rop'] = calculate_always_on_rop(calopeia_pipeline)
        result['calopeia_qty'] = calopeia_batch
        result['calopeia_mode'] = OperatingMode.BUILD

    return result


# ============================================================
# SIMPLE SYSTEM CALCULATION (Sorange, Fardo)
# ============================================================

def calculate_simple_system(
    warehouse: str,
    current_inventory: int,
    in_transit: int,
    regional_historical_demand: dict,
    current_day: int
) -> dict:
    """
    Calculates settings for simple single-warehouse systems (Sorange, Fardo).

    Both systems run Always On until their shutdown condition is met.

    Fardo carries an extra ceiling. Its demand is small and its island factory
    cannot ship anywhere else, so once its pipeline reaches
    `FARDO_WAREHOUSE_CAP` drums, further production would simply strand stock on
    the island.

    Returns:
        Dict with keys: 'rop', 'qty', 'mode'
    """
    # Hard shutdown: no production after HARD_SHUTDOWN_DAY
    # Set qty=0 to guarantee no production (game rule: order_quantity must be > 0)
    if current_day >= HARD_SHUTDOWN_DAY:
        return {
            'rop': 0,
            'qty': 0,
            'mode': OperatingMode.DRAWDOWN
        }

    pipeline = current_inventory + in_transit

    # Calculate dynamic batch size based on inventory level
    batch_size = calculate_batch_size(current_inventory, in_transit)

    # Fardo warehouse cap: don't order if pipeline >= cap
    if warehouse == "Fardo_WH" and pipeline >= FARDO_WAREHOUSE_CAP:
        return {
            'rop': 0,
            'qty': batch_size,
            'mode': OperatingMode.BUILD  # Still building, just at cap
        }

    remaining_demand = calculate_remaining_demand_for_warehouse(
        warehouse, regional_historical_demand, current_day
    )

    forecast = forecast_warehouse_demand(
        warehouse, regional_historical_demand, current_day, 30
    )
    current_rate = calculate_current_demand_rate(forecast)

    shutdown = should_shutdown(current_inventory, in_transit, remaining_demand, current_rate)

    if shutdown:
        # Set qty=0 to guarantee no production (game rule: order_quantity must be > 0)
        return {
            'rop': 0,
            'qty': 0,
            'mode': OperatingMode.DRAWDOWN
        }
    else:
        pipeline = current_inventory + in_transit
        return {
            'rop': calculate_always_on_rop(pipeline),
            'qty': batch_size,
            'mode': OperatingMode.BUILD
        }


# ============================================================
# ORDER QUANTITY AND SHIPPING
# ============================================================

def calculate_order_quantity(
    current_day: int,
    current_inventory: int,
    forecast_demand: np.ndarray,
    capacity: int,
) -> tuple[int, str]:
    """
    Calculates order quantity and shipping method.

    Normal operation: 200 drums via truck.
    Endgame (day 1430+): Use mail for responsiveness.
    """
    if current_day >= ENDGAME_START_DAY:
        return _calculate_endgame_order(
            current_day=current_day,
            current_inventory=current_inventory,
            forecast_demand=forecast_demand,
            capacity=capacity,
        )

    return (STANDARD_ORDER_QUANTITY, SHIPPING_TRUCK)


def _calculate_endgame_order(
    current_day: int,
    current_inventory: int,
    forecast_demand: np.ndarray,
    capacity: int,
) -> tuple[int, str]:
    """Calculates order quantity for endgame period."""
    days_until_end = GAME_END_DAY_DEMAND - current_day

    if days_until_end <= 0:
        return (0, SHIPPING_MAIL)

    remaining_demand = np.sum(forecast_demand[:min(days_until_end, len(forecast_demand))])
    safety_buffer = SHUTDOWN_SAFETY_DAYS * calculate_current_demand_rate(forecast_demand)

    inventory_gap = remaining_demand + safety_buffer - current_inventory

    if inventory_gap <= 0:
        return (0, SHIPPING_MAIL)

    # Use mail for quick delivery in endgame
    return (int(np.ceil(inventory_gap)), SHIPPING_MAIL)


# ============================================================
# LEAD TIME CALCULATION
# ============================================================

def calculate_effective_lead_time(
    order_quantity: int,
    capacity: int,
    shipping_method: str = SHIPPING_TRUCK,
) -> int:
    """
    Calculates effective lead time: production time + shipping time.
    """
    production_time = int(np.ceil(order_quantity / capacity))

    if shipping_method == SHIPPING_MAIL:
        shipping_time = SHIPPING_TIME_MAIL
    else:
        shipping_time = SHIPPING_TIME_TRUCK

    return production_time + shipping_time


def calculate_route_lead_time(
    factory: str,
    warehouse: str,
    order_quantity: int,
    capacity: int,
    shipping_method: ShippingMethod = ShippingMethod.TRUCK,
) -> int:
    """Calculates effective lead time for a specific route."""
    production_time = int(np.ceil(order_quantity / capacity))
    shipping_time = NETWORK.get_lead_time(factory, warehouse, shipping_method)

    return production_time + shipping_time


# ============================================================
# DEMAND STATISTICS
# ============================================================

def calculate_demand_statistics(
    historical_demand: pd.DataFrame,
    window_days: int = None,
    column: str = None
) -> dict:
    """
    Calculates demand statistics over a specified window.

    Returns dict with: mean, std, min, max, cv
    """
    demand_series = _extract_demand_series(historical_demand, column)

    if window_days is not None:
        window_data = demand_series[-window_days:] if len(demand_series) >= window_days else demand_series
    else:
        window_data = demand_series

    if len(window_data) == 0:
        return {'mean': 0, 'std': 0, 'min': 0, 'max': 0, 'cv': 0}

    mean_demand = np.mean(window_data)
    std_demand = np.std(window_data, ddof=1) if len(window_data) > 1 else 0
    cv = std_demand / mean_demand if mean_demand > 0 else 0

    return {
        'mean': mean_demand,
        'std': std_demand,
        'min': np.min(window_data),
        'max': np.max(window_data),
        'cv': cv
    }


# ============================================================
# PRIORITY CALCULATION (for multi-warehouse systems)
# ============================================================

def calculate_days_of_supply(
    warehouse_inventory: int,
    in_transit_to_warehouse: int,
    forecast_demand: np.ndarray,
    safety_stock: int = 0,
) -> float:
    """
    Calculates days of supply for a specific warehouse.
    """
    total_inventory = warehouse_inventory + in_transit_to_warehouse
    available_inventory = max(0, total_inventory - safety_stock)

    if len(forecast_demand) == 0:
        return float('inf')

    avg_daily_demand = np.mean(forecast_demand[:min(30, len(forecast_demand))])

    if avg_daily_demand <= 0:
        return float('inf')

    return available_inventory / avg_daily_demand


# ============================================================
# STOCKOUT DETECTION
# ============================================================

def is_stockout_imminent(
    warehouse_inventory: int,
    in_transit_inventory: int,
    forecast_demand: np.ndarray,
    capacity: int,
    order_quantity: int = STANDARD_ORDER_QUANTITY,
) -> bool:
    """
    Checks if a stockout is imminent within truck shipping lead time.
    Used for emergency mail shipping trigger.
    """
    truck_lead_time = calculate_effective_lead_time(
        order_quantity=order_quantity,
        capacity=capacity,
        shipping_method=SHIPPING_TRUCK,
    )

    check_horizon = min(truck_lead_time, len(forecast_demand))
    if check_horizon == 0:
        return False

    projected_inventory = warehouse_inventory

    for day in range(check_horizon):
        projected_inventory -= forecast_demand[day]

        if day == SHIPPING_TIME_TRUCK - 1:
            projected_inventory += in_transit_inventory

        if projected_inventory <= 0:
            return True

    return False
