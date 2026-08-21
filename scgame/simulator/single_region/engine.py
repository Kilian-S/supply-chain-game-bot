"""Offline simulation of the Single-Region Run game mechanics.

The engine reproduces the parts of the game that the strategy interacts with:
one factory producing serially in batches, a seven-day truck or one-day mail
link to one warehouse, demand that is lost rather than backordered when it
cannot be met the same day, and a cash balance that earns daily compound
interest.

Accounting follows the assignment notes exactly. Production is charged when a
batch starts, at $2,000 for the batch plus $900 for each drum in it. Shipping is
charged when a batch completes, at $15,000 per truck or $150 per drum by mail.
Customer fulfilment is charged at $150 for every drum sold. Holding is charged
daily on drums resting in the warehouse. Omitting either the fixed batch charge
or the fulfilment charge overstates profit substantially, so both are modelled.

The order of events within a day is the order the game uses. Shipments land
first, so a delivery arriving today can serve today's customers. Demand is then
served from whatever is on the shelf. Batches finishing production are dispatched
next, and finally the reorder point is tested to decide whether to start another
batch.
"""

import numpy as np
from dataclasses import dataclass
from typing import List

from ...common.economics import (
    GAME_START_DAY,
    GAME_END_DAY,
    REVENUE_PER_DRUM,
    FIXED_COST_PER_BATCH,
    VARIABLE_COST_PER_DRUM,
    FULFILMENT_COST_SAME_REGION,
    HOLDING_COST_PER_DRUM_PER_DAY,
    TRUCK_CAPACITY,
    TRUCK_COST_SAME_REGION,
    MAIL_COST_SAME_REGION,
    SHIPPING_DAYS_TRUCK,
    SHIPPING_DAYS_MAIL,
    CAPACITY_BUILD_DAYS,
    FACTORY_COST_PER_CAPACITY_UNIT,
    DAILY_INTEREST_RATE,
    STOCKOUT_COST_PER_DRUM,
)


@dataclass
class Shipment:
    """Drums dispatched from the factory and not yet received."""

    quantity: int
    arrival_day: float


@dataclass
class WorkInProgress:
    """A batch currently being produced."""

    quantity: int
    completion_day: float
    shipping_method: str


@dataclass
class DailyRecord:
    """Everything observable about one simulated day."""

    day: int
    warehouse_inventory: int
    in_transit_inventory: int
    work_in_progress: int
    demand: int
    sales: int
    stockout: int
    capacity: int
    reorder_point: int
    order_quantity: int
    shipping_method: str
    mode: str = ""
    safety_stock: int = 0
    future_deficit: int = 0
    revenue: float = 0.0
    production_cost: float = 0.0
    shipping_cost: float = 0.0
    fulfilment_cost: float = 0.0
    holding_cost: float = 0.0
    interest: float = 0.0
    cash: float = 0.0

    @property
    def total_inventory(self) -> int:
        """Drums on the shelf plus drums in transit."""
        return self.warehouse_inventory + self.in_transit_inventory


@dataclass
class SimulationConfig:
    """Scenario parameters for a Single-Region Run simulation.

    The defaults reproduce the run that was played. Final capacity of 50 drums
    per day and capital expenditure of $1,000,000 are stated in the coursework
    report and the presentation, and $1,000,000 buys 20 units of capacity at
    $50,000 each, which places the starting capacity at 30.
    """

    start_day: int = GAME_START_DAY
    end_day: int = GAME_END_DAY

    starting_capacity: int = 30
    expanded_capacity: int = 50
    capacity_online_delay: int = CAPACITY_BUILD_DAYS

    initial_warehouse: int = 500
    initial_in_transit: int = 0

    initial_reorder_point: int = 300
    initial_order_quantity: int = 200
    initial_shipping_method: str = "TRUCK"

    starting_cash: float = 2_000_000.0
    demand_path: str = None


class SingleRegionEngine:
    """Simulates the Single-Region Run day by day."""

    def __init__(self, config: SimulationConfig, demand: np.ndarray):
        """
        Args:
            config: Scenario parameters.
            demand: Daily demand indexed from day 1, so element 0 is day 1. Must
                cover at least up to `config.end_day`.
        """
        if len(demand) < config.end_day:
            raise ValueError(
                f"Demand series covers {len(demand)} days but the simulation "
                f"runs to day {config.end_day}."
            )

        self.config = config
        self.demand = demand
        self._reset()

    def _reset(self):
        self.current_day = self.config.start_day
        self.warehouse_inventory = self.config.initial_warehouse
        self.in_transit: List[Shipment] = []
        self.work_in_progress: List[WorkInProgress] = []
        self.factory_free_day = float(self.config.start_day)

        if self.config.initial_in_transit > 0:
            self.in_transit.append(
                Shipment(self.config.initial_in_transit, self.current_day + 3)
            )

        self.reorder_point = self.config.initial_reorder_point
        self.order_quantity = self.config.initial_order_quantity
        self.shipping_method = self.config.initial_shipping_method

        capacity_added = max(
            0, self.config.expanded_capacity - self.config.starting_capacity
        )
        self.total_capex = capacity_added * FACTORY_COST_PER_CAPACITY_UNIT

        self.cash = self.config.starting_cash - self.total_capex
        self.total_revenue = 0.0
        self.total_production_cost = 0.0
        self.total_fixed_production_cost = 0.0
        self.total_variable_production_cost = 0.0
        self.total_shipping_cost = 0.0
        self.total_fulfilment_cost = 0.0
        self.total_holding_cost = 0.0
        self.total_interest = 0.0
        self.total_stockouts = 0
        self.total_demand = 0
        self.total_sales = 0

        self.daily_records: List[DailyRecord] = []
        self.last_mode = ""
        self.last_safety_stock = 0
        self.last_future_deficit = 0

    # ------------------------------------------------------------------
    # Observable state
    # ------------------------------------------------------------------

    @property
    def capacity(self) -> int:
        """Daily production capacity, which steps up once the expansion lands."""
        online_day = self.config.start_day + self.config.capacity_online_delay
        return (
            self.config.expanded_capacity
            if self.current_day >= online_day
            else self.config.starting_capacity
        )

    @property
    def in_transit_total(self) -> int:
        """Drums dispatched and not yet received."""
        return sum(shipment.quantity for shipment in self.in_transit)

    @property
    def work_in_progress_total(self) -> int:
        """Drums currently being produced."""
        return sum(batch.quantity for batch in self.work_in_progress)

    @property
    def total_inventory(self) -> int:
        """The quantity the game compares against the reorder point."""
        return self.warehouse_inventory + self.in_transit_total

    @property
    def is_game_over(self) -> bool:
        """Whether the simulation has passed its final day."""
        return self.current_day > self.config.end_day

    def get_demand_for_day(self, day: int) -> int:
        """Return demand for a given day, indexed from day 1."""
        return int(self.demand[day - 1])

    def get_historical_demand(self):
        """Return demand observed up to and including today, as the bot sees it."""
        import pandas as pd

        days = np.arange(1, self.current_day + 1)
        return pd.DataFrame({"day": days, "demand": self.demand[: self.current_day]})

    # ------------------------------------------------------------------
    # Settings, written by the bot
    # ------------------------------------------------------------------

    def set_reorder_point(self, reorder_point: int):
        """Set the level at or below which production is triggered."""
        self.reorder_point = max(0, int(reorder_point))

    def set_order_quantity(self, quantity: int):
        """Set the batch size. Zero suppresses production entirely."""
        self.order_quantity = max(0, int(quantity))

    def set_shipping_method(self, method: str):
        """Set the outbound shipping method, either TRUCK or MAIL."""
        if method not in ("TRUCK", "MAIL"):
            raise ValueError(f"Unknown shipping method: {method}")
        self.shipping_method = method

    def set_diagnostics(self, mode: str, safety_stock: int, future_deficit: int):
        """Record the bot's internal reasoning for display and analysis."""
        self.last_mode = mode
        self.last_safety_stock = safety_stock
        self.last_future_deficit = future_deficit

    # ------------------------------------------------------------------
    # One day
    # ------------------------------------------------------------------

    def step(self) -> DailyRecord:
        """Advance the simulation by one day and return that day's record."""
        if self.is_game_over:
            raise RuntimeError("The simulation has already ended.")

        # 1. Receive shipments that land today, before any selling happens.
        arrived = [
            shipment
            for shipment in self.in_transit
            if shipment.arrival_day < self.current_day + 1
        ]
        for shipment in arrived:
            self.warehouse_inventory += shipment.quantity
            self.in_transit.remove(shipment)

        # 2. Serve today's demand from the shelf. Anything unserved is lost to a
        #    competitor rather than carried, because the game gives one day to
        #    fill an order.
        demand = self.get_demand_for_day(self.current_day)
        sales = min(demand, self.warehouse_inventory)
        stockout = demand - sales
        self.warehouse_inventory -= sales

        self.total_demand += demand
        self.total_sales += sales
        self.total_stockouts += stockout

        revenue = sales * REVENUE_PER_DRUM
        fulfilment_cost = sales * FULFILMENT_COST_SAME_REGION

        # 3. Dispatch batches that finished production today.
        shipping_cost = 0.0
        completed = [
            batch
            for batch in self.work_in_progress
            if batch.completion_day < self.current_day + 1
        ]
        for batch in completed:
            self.work_in_progress.remove(batch)

            if batch.shipping_method == "MAIL":
                shipping_days = SHIPPING_DAYS_MAIL
                shipping_cost += batch.quantity * MAIL_COST_SAME_REGION
            else:
                shipping_days = SHIPPING_DAYS_TRUCK
                trucks = int(np.ceil(batch.quantity / TRUCK_CAPACITY))
                shipping_cost += trucks * TRUCK_COST_SAME_REGION

            self.in_transit.append(
                Shipment(batch.quantity, batch.completion_day + shipping_days)
            )

        # 4. Start a batch if the reorder point allows it. The factory builds one
        #    batch at a time, so nothing starts while a batch is in progress.
        production_cost = 0.0
        if (
            not self.work_in_progress
            and self.order_quantity > 0
            and self.total_inventory <= self.reorder_point
        ):
            production_days = self.order_quantity / self.capacity
            start_day = max(self.factory_free_day, float(self.current_day))
            completion_day = start_day + production_days

            self.work_in_progress.append(
                WorkInProgress(self.order_quantity, completion_day, self.shipping_method)
            )
            self.factory_free_day = completion_day

            fixed_cost = FIXED_COST_PER_BATCH
            variable_cost = self.order_quantity * VARIABLE_COST_PER_DRUM
            production_cost = fixed_cost + variable_cost
            self.total_fixed_production_cost += fixed_cost
            self.total_variable_production_cost += variable_cost

        # 5. Charge holding on whatever is left resting in the warehouse.
        holding_cost = self.warehouse_inventory * HOLDING_COST_PER_DRUM_PER_DAY

        self.total_revenue += revenue
        self.total_production_cost += production_cost
        self.total_shipping_cost += shipping_cost
        self.total_fulfilment_cost += fulfilment_cost
        self.total_holding_cost += holding_cost

        # 6. Settle cash and accrue interest on the closing balance.
        daily_profit = (
            revenue - production_cost - shipping_cost - fulfilment_cost - holding_cost
        )
        self.cash += daily_profit
        interest = self.cash * DAILY_INTEREST_RATE
        self.cash += interest
        self.total_interest += interest

        record = DailyRecord(
            day=self.current_day,
            warehouse_inventory=self.warehouse_inventory,
            in_transit_inventory=self.in_transit_total,
            work_in_progress=self.work_in_progress_total,
            demand=demand,
            sales=sales,
            stockout=stockout,
            capacity=self.capacity,
            reorder_point=self.reorder_point,
            order_quantity=self.order_quantity,
            shipping_method=self.shipping_method,
            mode=self.last_mode,
            safety_stock=self.last_safety_stock,
            future_deficit=self.last_future_deficit,
            revenue=revenue,
            production_cost=production_cost,
            shipping_cost=shipping_cost,
            fulfilment_cost=fulfilment_cost,
            holding_cost=holding_cost,
            interest=interest,
            cash=self.cash,
        )
        self.daily_records.append(record)
        self.current_day += 1
        return record

    # ------------------------------------------------------------------
    # Results
    # ------------------------------------------------------------------

    @property
    def item_fill_rate(self) -> float:
        """Fraction of demanded drums that were actually sold."""
        if self.total_demand == 0:
            return 1.0
        return self.total_sales / self.total_demand

    @property
    def total_profit(self) -> float:
        """Cash generated over the run, excluding the opening balance."""
        return self.cash - self.config.starting_cash

    def financial_summary(self) -> dict:
        """Return every cumulative figure the run produced."""
        return {
            "total_revenue": self.total_revenue,
            "total_fixed_production_cost": self.total_fixed_production_cost,
            "total_variable_production_cost": self.total_variable_production_cost,
            "total_shipping_cost": self.total_shipping_cost,
            "total_fulfilment_cost": self.total_fulfilment_cost,
            "total_holding_cost": self.total_holding_cost,
            "total_capex": self.total_capex,
            "total_interest": self.total_interest,
            "total_profit": self.total_profit,
            "cash": self.cash,
            "total_demand": self.total_demand,
            "total_sales": self.total_sales,
            "total_stockouts": self.total_stockouts,
            "item_fill_rate": self.item_fill_rate,
            "stockout_opportunity_cost": self.total_stockouts * STOCKOUT_COST_PER_DRUM,
            "leftover_inventory": self.warehouse_inventory + self.in_transit_total,
        }
