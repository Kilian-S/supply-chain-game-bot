# Batch scenario runner for comparing different strategies.
# Enables testing build timing, capacity sizing, and other strategic decisions.

import pandas as pd
from typing import List, Dict, Callable
from dataclasses import dataclass, field
import time

from .engine import NetworkEngine
from .build_schedule import BuildSchedule
from .controller import SimulatedNetworkController


@dataclass
class ScenarioResult:
    """Results from running one scenario."""
    scenario_name: str
    total_profit: float
    cash: float
    total_revenue: float
    total_stockouts: int
    total_capex: float
    total_production_cost: float
    total_fulfilment_cost: float
    total_shipping_cost: float
    total_holding_cost: float
    total_interest: float
    run_time_seconds: float

    # Per-warehouse final state
    final_warehouse_inventory: Dict[str, int] = field(default_factory=dict)

    # Per-region stockout totals
    regional_stockouts: Dict[str, int] = field(default_factory=dict)


class ScenarioRunner:
    """
    Runs multiple scenarios and compares results.

    Use cases:
    - Compare different build schedules
    - Test bot strategies
    - Sensitivity analysis
    """

    def __init__(self, demand_folder: str = None):
        """
        Initialise the scenario runner.

        Args:
            demand_folder: Path to demand Excel files.
        """
        self.demand_folder = demand_folder

    def run_scenario(
        self,
        build_schedule: BuildSchedule,
        bot_callback: Callable[[SimulatedNetworkController], None] = None,
        verbose: bool = False,
    ) -> ScenarioResult:
        """
        Run a single scenario.

        Args:
            build_schedule: Build schedule defining factory/warehouse timing
            bot_callback: Optional function called each day with the controller.
                         Use this to run your bot logic.
                         If None, runs with default settings (no bot).
            verbose: Print progress every 100 days

        Returns:
            ScenarioResult with financial and operational metrics.
        """
        start_time = time.time()

        # Create engine and controller
        engine = NetworkEngine(
            build_schedule=build_schedule,
            demand_folder=self.demand_folder,
        )
        controller = SimulatedNetworkController(engine=engine)
        controller.login()

        # Track regional stockouts
        regional_stockouts = {region: 0 for region in ["Calopeia", "Sorange", "Tyran", "Entworpe", "Fardo"]}

        # Run simulation
        day_count = 0
        while not engine.is_game_over:
            # Run bot logic if provided
            if bot_callback:
                try:
                    bot_callback(controller)
                except Exception as e:
                    if verbose:
                        print(f"Day {engine.current_day}: Bot error: {e}")

            # Step simulation
            record = engine.step()
            day_count += 1

            # Track regional stockouts
            for region, result in record.fulfilment_results.items():
                regional_stockouts[region] += result.stockout

            if verbose and day_count % 100 == 0:
                print(f"Day {record.day}: Cash=${engine.cash:,.0f}, Stockouts={engine.total_stockouts}")

        run_time = time.time() - start_time

        # Compile results
        summary = engine.get_financial_summary()

        result = ScenarioResult(
            scenario_name=build_schedule.name,
            total_profit=summary['total_profit'],
            cash=summary['cash'],
            total_revenue=summary['total_revenue'],
            total_stockouts=summary['total_stockouts'],
            total_capex=summary['total_capex'],
            total_production_cost=summary['total_production_cost'],
            total_fulfilment_cost=summary['total_fulfilment_cost'],
            total_shipping_cost=summary['total_shipping_cost'],
            total_holding_cost=summary['total_holding_cost'],
            total_interest=summary['total_interest'],
            run_time_seconds=run_time,
            final_warehouse_inventory={
                wh: engine.warehouses[wh].inventory
                for wh in engine.warehouses
            },
            regional_stockouts=regional_stockouts,
        )

        return result

    def compare_schedules(
        self,
        schedules: List[BuildSchedule],
        bot_callback: Callable[[SimulatedNetworkController], None] = None,
        verbose: bool = True,
    ) -> pd.DataFrame:
        """
        Compare multiple build schedules.

        Args:
            schedules: List of BuildSchedule objects to compare
            bot_callback: Bot logic to apply (same for all scenarios)
            verbose: Print progress

        Returns:
            DataFrame comparing all scenarios.
        """
        results = []

        for i, schedule in enumerate(schedules):
            if verbose:
                print(f"\n[{i+1}/{len(schedules)}] Running: {schedule.name}")

            result = self.run_scenario(
                build_schedule=schedule,
                bot_callback=bot_callback,
                verbose=verbose,
            )
            results.append(result)

            if verbose:
                print(f"  Profit: ${result.total_profit:,.0f}, Stockouts: {result.total_stockouts}")

        # Convert to DataFrame
        df = pd.DataFrame([
            {
                'scenario': r.scenario_name,
                'profit': r.total_profit,
                'cash': r.cash,
                'revenue': r.total_revenue,
                'stockouts': r.total_stockouts,
                'capex': r.total_capex,
                'production_cost': r.total_production_cost,
                'fulfilment_cost': r.total_fulfilment_cost,
                'shipping_cost': r.total_shipping_cost,
                'holding_cost': r.total_holding_cost,
                'interest': r.total_interest,
                'run_time': r.run_time_seconds,
            }
            for r in results
        ])

        return df

    def run_with_bot(
        self,
        build_schedule: BuildSchedule,
        bot_class,
        verbose: bool = True,
    ) -> ScenarioResult:
        """
        Run a scenario with the actual SupplyChainBot class.

        Args:
            build_schedule: Build schedule to use
            bot_class: The SupplyChainBot class (not an instance)
            verbose: Print progress

        Returns:
            ScenarioResult
        """
        engine = NetworkEngine(
            build_schedule=build_schedule,
            demand_folder=self.demand_folder,
        )
        controller = SimulatedNetworkController(engine=engine)

        # Create bot with the simulated controller
        bot = bot_class(controller)

        # Track regional stockouts
        regional_stockouts = {region: 0 for region in ["Calopeia", "Sorange", "Tyran", "Entworpe", "Fardo"]}

        start_time = time.time()

        controller.login()

        while not engine.is_game_over:
            try:
                # Run bot cycle
                bot.run_cycle()
            except Exception as e:
                if verbose:
                    print(f"Day {engine.current_day}: Bot error: {e}")

            # Step simulation
            record = engine.step()

            # Track regional stockouts
            for region, result in record.fulfilment_results.items():
                regional_stockouts[region] += result.stockout

            if verbose and (engine.current_day - 730) % 100 == 0:
                print(f"Day {record.day}: Cash=${engine.cash:,.0f}, Stockouts={engine.total_stockouts}")

        run_time = time.time() - start_time
        summary = engine.get_financial_summary()

        return ScenarioResult(
            scenario_name=build_schedule.name,
            total_profit=summary['total_profit'],
            cash=summary['cash'],
            total_revenue=summary['total_revenue'],
            total_stockouts=summary['total_stockouts'],
            total_capex=summary['total_capex'],
            total_production_cost=summary['total_production_cost'],
            total_fulfilment_cost=summary['total_fulfilment_cost'],
            total_shipping_cost=summary['total_shipping_cost'],
            total_holding_cost=summary['total_holding_cost'],
            total_interest=summary['total_interest'],
            run_time_seconds=run_time,
            final_warehouse_inventory={
                wh: engine.warehouses[wh].inventory
                for wh in engine.warehouses
            },
            regional_stockouts=regional_stockouts,
        )


def create_capacity_test_schedules(
    base_schedule: BuildSchedule,
    factory: str,
    capacities: List[int],
) -> List[BuildSchedule]:
    """
    Create schedules to test different capacity levels for a factory.

    Args:
        base_schedule: Base schedule to modify
        factory: Factory to vary capacity for
        capacities: List of capacity values to test

    Returns:
        List of BuildSchedule objects
    """
    schedules = []

    for cap in capacities:
        new_capacities = dict(base_schedule.factory_capacities)
        new_capacities[factory] = cap

        schedule = BuildSchedule(
            name=f"{factory}_cap_{cap}",
            factory_orders=dict(base_schedule.factory_orders),
            factory_capacities=new_capacities,
            factory_starting_capacities=dict(base_schedule.factory_starting_capacities),
            capacity_expansion_orders=dict(base_schedule.capacity_expansion_orders),
            warehouse_orders=dict(base_schedule.warehouse_orders),
            initial_inventory=dict(base_schedule.initial_inventory),
        )
        schedules.append(schedule)

    return schedules


def create_timing_test_schedules(
    base_schedule: BuildSchedule,
    factory: str,
    order_days: List[int],
) -> List[BuildSchedule]:
    """
    Create schedules to test different build timing for a factory.

    Args:
        base_schedule: Base schedule to modify
        factory: Factory to vary timing for
        order_days: List of order days to test

    Returns:
        List of BuildSchedule objects
    """
    schedules = []

    for day in order_days:
        new_orders = dict(base_schedule.factory_orders)
        new_orders[factory] = day

        # Also adjust warehouse timing if there's a matching warehouse
        new_wh_orders = dict(base_schedule.warehouse_orders)
        wh_name = factory.replace("_Factory", "_WH")
        if wh_name in new_wh_orders:
            new_wh_orders[wh_name] = day

        schedule = BuildSchedule(
            name=f"{factory}_day_{day}",
            factory_orders=new_orders,
            factory_capacities=dict(base_schedule.factory_capacities),
            factory_starting_capacities=dict(base_schedule.factory_starting_capacities),
            capacity_expansion_orders=dict(base_schedule.capacity_expansion_orders),
            warehouse_orders=new_wh_orders,
            initial_inventory=dict(base_schedule.initial_inventory),
        )
        schedules.append(schedule)

    return schedules
