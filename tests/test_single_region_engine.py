"""The Single-Region Run engine must charge every cost and time every event.

An earlier version of this engine served demand before receiving that day's
deliveries, and omitted both the customer fulfilment charge and the fixed charge
on each production batch. Together those three defects understated the fill rate
and overstated profit by several million dollars, so each has a test here.
"""

import numpy as np
import pytest

from scgame.common import economics
from scgame.simulator.single_region.engine import (
    SimulationConfig,
    SingleRegionEngine,
    Shipment,
)


def build_engine(demand_per_day=10, **overrides) -> SingleRegionEngine:
    """Return an engine over flat demand, with production switched off by default."""
    settings = {
        "initial_warehouse": 0,
        "initial_reorder_point": 0,
        "initial_order_quantity": 0,
        "starting_capacity": 50,
        "expanded_capacity": 50,
    }
    settings.update(overrides)
    config = SimulationConfig(**settings)
    demand = np.full(config.end_day, demand_per_day, dtype=float)
    return SingleRegionEngine(config, demand)


def test_a_delivery_arriving_today_can_serve_todays_demand():
    """Stock landing on day D must be sellable on day D.

    The game fills an order from whatever is on the shelf when the order
    arrives, and a delivery received that morning is on the shelf. Serving
    demand before receiving deliveries would report a stockout that never
    happened.
    """
    engine = build_engine(demand_per_day=10)
    engine.in_transit.append(Shipment(quantity=10, arrival_day=engine.current_day))

    record = engine.step()

    assert record.sales == 10
    assert record.stockout == 0


def test_demand_beyond_available_stock_is_lost_rather_than_carried():
    """Unmet demand goes to a competitor and never returns as a backorder."""
    engine = build_engine(demand_per_day=10, initial_warehouse=4)

    first = engine.step()
    assert first.sales == 4
    assert first.stockout == 6

    second = engine.step()
    assert second.demand == 10, "Yesterday's shortfall must not be added to today"


def test_every_drum_sold_carries_the_customer_fulfilment_charge():
    """Selling a drum costs $150 to deliver to the customer."""
    engine = build_engine(demand_per_day=10, initial_warehouse=100)

    record = engine.step()

    assert record.sales == 10
    assert record.fulfilment_cost == pytest.approx(
        10 * economics.FULFILMENT_COST_SAME_REGION
    )


def test_starting_a_batch_charges_both_the_fixed_and_the_variable_cost():
    """A batch costs $2,000 plus $900 for each drum in it, charged at the start."""
    engine = build_engine(
        demand_per_day=0,
        initial_warehouse=0,
        initial_reorder_point=100,
        initial_order_quantity=200,
    )

    record = engine.step()

    expected = economics.FIXED_COST_PER_BATCH + 200 * economics.VARIABLE_COST_PER_DRUM
    assert record.production_cost == pytest.approx(expected)
    assert engine.total_fixed_production_cost == pytest.approx(
        economics.FIXED_COST_PER_BATCH
    )


def test_the_factory_builds_one_batch_at_a_time():
    """A second batch cannot start while the first is still in production."""
    engine = build_engine(
        demand_per_day=0,
        initial_reorder_point=10_000,
        initial_order_quantity=200,
        starting_capacity=10,
        expanded_capacity=10,
    )

    engine.step()
    assert len(engine.work_in_progress) == 1

    engine.step()
    assert len(engine.work_in_progress) == 1, "The factory is not free yet"


def test_a_zero_order_quantity_stops_production_outright():
    """Setting the batch size to zero is how the endgame halts the factory."""
    engine = build_engine(
        demand_per_day=0,
        initial_reorder_point=10_000,
        initial_order_quantity=0,
    )

    engine.step()

    assert engine.work_in_progress == []
    assert engine.total_production_cost == 0


def test_truck_shipping_is_charged_by_the_truck_and_mail_by_the_drum():
    """Truck charges a flat rate up to 200 drums; mail charges per drum."""
    by_truck = build_engine(
        demand_per_day=0, initial_reorder_point=10_000, initial_order_quantity=200
    )
    by_truck.set_shipping_method("TRUCK")
    while not by_truck.in_transit:
        by_truck.step()
    assert by_truck.total_shipping_cost == pytest.approx(
        economics.TRUCK_COST_SAME_REGION
    )

    by_mail = build_engine(
        demand_per_day=0, initial_reorder_point=10_000, initial_order_quantity=200
    )
    by_mail.set_shipping_method("MAIL")
    while not by_mail.in_transit:
        by_mail.step()
    assert by_mail.total_shipping_cost == pytest.approx(
        200 * economics.MAIL_COST_SAME_REGION
    )


def test_capacity_steps_up_only_once_the_expansion_lands():
    """Capacity bought on day 730 is unavailable until day 820."""
    engine = build_engine(starting_capacity=30, expanded_capacity=50)
    online_day = engine.config.start_day + engine.config.capacity_online_delay

    assert engine.capacity == 30

    while engine.current_day < online_day:
        engine.step()

    assert engine.capacity == 50


def test_the_fill_rate_counts_drums_rather_than_days():
    """Item fill rate is drums sold divided by drums demanded."""
    engine = build_engine(demand_per_day=10, initial_warehouse=25)

    for _ in range(3):
        engine.step()

    assert engine.total_demand == 30
    assert engine.total_sales == 25
    assert engine.item_fill_rate == pytest.approx(25 / 30)


def test_the_engine_refuses_a_demand_series_that_is_too_short():
    """Running past the end of the demand data must fail loudly, not silently."""
    config = SimulationConfig()
    with pytest.raises(ValueError, match="Demand series"):
        SingleRegionEngine(config, np.full(100, 10.0))
