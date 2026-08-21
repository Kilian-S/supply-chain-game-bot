"""The Network Run topology, fulfilment policy, and Always On rules."""

import numpy as np
import pandas as pd
import pytest

from scgame.common import economics
from scgame.network import calculator as calc
from scgame.network.config import NETWORK, ShippingMethod
from scgame.simulator.network.fulfilment import FulfilmentEngine


# --- Topology and route pricing -------------------------------------------


def test_serving_a_region_from_its_own_warehouse_is_the_cheapest_option():
    """Distance is what fulfilment is priced on, so local is always cheapest."""
    local = NETWORK.get_fulfilment_cost("Calopeia_WH", "Calopeia")
    cross = NETWORK.get_fulfilment_cost("Calopeia_WH", "Tyran")
    island = NETWORK.get_fulfilment_cost("Calopeia_WH", "Fardo")
    assert local < cross < island


def test_reaching_the_island_costs_the_intercontinental_rate():
    """Any drum crossing to or from Fardo pays the highest rate."""
    assert NETWORK.get_fulfilment_cost("Fardo_WH", "Calopeia") == (
        economics.FULFILMENT_COST_TO_FARDO
    )


def test_shipping_to_tyran_costs_more_and_no_longer_than_shipping_within_calopeia():
    """Crossing a region raises the price but not the seven-day truck time."""
    local = NETWORK.get_route("Calopeia_Factory", "Calopeia_WH")
    cross = NETWORK.get_route("Calopeia_Factory", "Tyran_WH")

    local_truck = local.shipping_options[ShippingMethod.TRUCK]
    cross_truck = cross.shipping_options[ShippingMethod.TRUCK]

    assert cross_truck.cost_per_truck > local_truck.cost_per_truck
    assert cross_truck.lead_time_days == local_truck.lead_time_days


def test_a_part_filled_truck_is_charged_as_a_whole_truck():
    """Truck pricing is per truck, which is why batches are multiples of 200."""
    one_drum = NETWORK.get_shipping_cost(
        "Calopeia_Factory", "Calopeia_WH", ShippingMethod.TRUCK, 1
    )
    full_truck = NETWORK.get_shipping_cost(
        "Calopeia_Factory", "Calopeia_WH", ShippingMethod.TRUCK, 200
    )
    assert one_drum == full_truck


def test_two_hundred_and_one_drums_need_two_trucks():
    """One drum over the limit doubles the shipping bill."""
    cost = NETWORK.get_shipping_cost(
        "Calopeia_Factory", "Calopeia_WH", ShippingMethod.TRUCK, 201
    )
    assert cost == 2 * economics.TRUCK_COST_SAME_REGION


def test_the_three_systems_share_no_facilities():
    """Systems are independent so that a failure in one cannot starve another."""
    warehouses = [set(system.warehouse_names) for system in NETWORK.systems.values()]
    for index, first in enumerate(warehouses):
        for second in warehouses[index + 1:]:
            assert not first & second


def test_entworpe_is_served_without_a_warehouse_of_its_own():
    """Its lumpy demand did not justify the capital, so Calopeia covers it."""
    assert NETWORK.get_warehouse_for_region("Entworpe") == "Calopeia_WH"
    assert "Entworpe" not in {w.region_id for w in NETWORK.warehouses.values()}


# --- Fulfilment policy -----------------------------------------------------


def test_demand_is_served_from_the_cheapest_warehouse_that_holds_stock():
    """This is the nearest policy the game was configured with."""
    engine = FulfilmentEngine()
    inventories = {"Calopeia_WH": 100, "Sorange_WH": 100, "Tyran_WH": 100}

    result, remaining = engine.fulfil_demand("Tyran", 50, inventories)

    assert result.fulfilled == 50
    assert remaining["Tyran_WH"] == 50, "Tyran should serve its own region first"
    assert remaining["Calopeia_WH"] == 100


def test_a_second_warehouse_covers_what_the_nearest_one_cannot():
    """Cross-fulfilment is what kept the network fill rate high."""
    engine = FulfilmentEngine()
    inventories = {"Calopeia_WH": 100, "Tyran_WH": 10}

    result, remaining = engine.fulfil_demand("Tyran", 50, inventories)

    assert result.fulfilled == 50
    assert remaining["Tyran_WH"] == 0
    assert remaining["Calopeia_WH"] == 60


def test_demand_beyond_the_whole_network_is_recorded_as_lost():
    """Nothing is backordered, so unmet demand is counted immediately."""
    engine = FulfilmentEngine()
    result, _ = engine.fulfil_demand("Tyran", 50, {"Tyran_WH": 20})

    assert result.fulfilled == 20
    assert result.stockout == 30


def test_the_island_is_never_served_from_the_mainland():
    """Fardo is isolated deliberately, because crossing wipes out the margin."""
    engine = FulfilmentEngine()
    result, remaining = engine.fulfil_demand(
        "Fardo", 50, {"Calopeia_WH": 1000, "Fardo_WH": 0}
    )

    assert result.fulfilled == 0
    assert result.stockout == 50
    assert remaining["Calopeia_WH"] == 1000


def test_the_fulfilment_charge_follows_the_warehouse_that_actually_served():
    """Splitting an order across warehouses must price each part separately."""
    engine = FulfilmentEngine()
    result, _ = engine.fulfil_demand("Tyran", 50, {"Calopeia_WH": 100, "Tyran_WH": 10})

    expected = (
        10 * economics.FULFILMENT_COST_SAME_REGION
        + 40 * economics.FULFILMENT_COST_CROSS_REGION
    )
    assert result.total_fulfilment_cost == pytest.approx(expected)


# --- Always On policy ------------------------------------------------------


def test_the_reorder_point_always_sits_above_the_current_pipeline():
    """This is what guarantees the factory never idles before shutdown."""
    for pipeline in (0, 500, 5000):
        assert calc.calculate_always_on_rop(pipeline) > pipeline


def test_production_stops_once_stock_covers_all_remaining_demand():
    """Every drum made after this point could not be sold."""
    assert calc.should_shutdown(
        current_inventory=1000,
        in_transit=200,
        remaining_demand=1000,
        current_demand_rate=20,
    )


def test_production_continues_while_demand_still_exceeds_stock():
    """Stopping early would strand demand that the factory could still meet."""
    assert not calc.should_shutdown(
        current_inventory=100,
        in_transit=0,
        remaining_demand=5000,
        current_demand_rate=20,
    )


def test_batch_size_rises_with_the_size_of_the_pipeline():
    """A bigger pipeline can absorb a bigger batch, spreading the order charge."""
    assert calc.calculate_batch_size(500, 0) == calc.STANDARD_ORDER_QUANTITY
    assert calc.calculate_batch_size(2000, 0) == calc.BATCH_SIZE_TIER_1
    assert calc.calculate_batch_size(5000, 0) == calc.BATCH_SIZE_TIER_2


def test_batch_size_is_judged_on_stock_and_stock_in_transit_together():
    """Drums already on their way count towards what the warehouse can absorb."""
    assert calc.calculate_batch_size(500, 600) == calc.calculate_batch_size(1100, 0)


def test_every_batch_size_fills_whole_trucks():
    """A batch that part fills a truck pays for space it does not use."""
    for batch in (
        calc.STANDARD_ORDER_QUANTITY,
        calc.BATCH_SIZE_TIER_1,
        calc.BATCH_SIZE_TIER_2,
    ):
        assert batch % economics.TRUCK_CAPACITY == 0


def test_no_system_produces_after_the_hard_shutdown_day():
    """A final backstop, in case the demand comparison is ever wrong."""
    settings = calc.calculate_simple_system(
        warehouse="Sorange_WH",
        current_inventory=0,
        in_transit=0,
        regional_historical_demand={},
        current_day=calc.HARD_SHUTDOWN_DAY,
    )
    assert settings["qty"] == 0
    assert settings["rop"] == 0


def test_the_island_stops_ordering_once_its_warehouse_is_full_enough():
    """Fardo cannot ship elsewhere, so surplus there is stranded outright."""
    settings = calc.calculate_simple_system(
        warehouse="Fardo_WH",
        current_inventory=calc.FARDO_WAREHOUSE_CAP,
        in_transit=0,
        regional_historical_demand={},
        current_day=900,
    )
    assert settings["rop"] == 0


def test_tyran_outranks_calopeia_only_while_it_sits_below_its_floor():
    """Priority decides which warehouse the shared factory serves first."""
    demand = {
        region: pd.DataFrame({"day": range(1, 901), "demand": [10.0] * 900})
        for region in ("Calopeia", "Sorange", "Tyran", "Entworpe", "Fardo")
    }

    short = calc.calculate_calopeia_system_allocation(
        calopeia_wh_inventory=500, calopeia_wh_in_transit=0,
        tyran_wh_inventory=0, tyran_wh_in_transit=0,
        factory_capacity=75, regional_historical_demand=demand, current_day=900,
    )
    assert short["tyran_priority"] > short["calopeia_priority"]

    stocked = calc.calculate_calopeia_system_allocation(
        calopeia_wh_inventory=500, calopeia_wh_in_transit=0,
        tyran_wh_inventory=calc.TYRAN_FLOOR, tyran_wh_in_transit=0,
        factory_capacity=75, regional_historical_demand=demand, current_day=900,
    )
    assert stocked["calopeia_priority"] > stocked["tyran_priority"]


# --- Regional forecasting --------------------------------------------------


def test_sorange_demand_is_forecast_from_its_fitted_growth_line():
    """Sorange grows steadily, so a linear fit beats a seasonal model."""
    forecast = calc.forecast_demand(
        region="Sorange",
        historical_demand=pd.DataFrame(),
        current_day=800,
        horizon_days=10,
    )
    expected_first = calc.SORANGE_SLOPE * (801 - calc.SORANGE_START_DAY) + calc.SORANGE_INTERCEPT
    assert forecast[0] == pytest.approx(expected_first)
    assert forecast[-1] > forecast[0], "Sorange demand grows over time"


def test_entworpe_is_forecast_as_a_flat_daily_rate():
    """Its orders arrive as occasional blocks that no daily model can time."""
    forecast = calc.forecast_demand(
        region="Entworpe",
        historical_demand=pd.DataFrame(),
        current_day=800,
        horizon_days=10,
    )
    assert forecast == pytest.approx(np.full(10, calc.ENTWORPE_DAILY_DEMAND))


def test_every_regional_forecast_declines_to_zero_by_the_end_of_the_game():
    """All demand falls linearly to zero between day 1430 and day 1460."""
    for region in ("Sorange", "Entworpe"):
        forecast = calc.forecast_demand(
            region=region,
            historical_demand=pd.DataFrame(),
            current_day=1455,
            horizon_days=10,
        )
        assert forecast[4] == pytest.approx(0.0)   # day 1460
        assert forecast[-1] == pytest.approx(0.0)  # past the end


def test_an_unknown_region_is_rejected_rather_than_guessed():
    """A typo in a region name must fail loudly."""
    with pytest.raises(ValueError, match="Unknown region"):
        calc.forecast_demand("Atlantis", pd.DataFrame(), 800, 10)
