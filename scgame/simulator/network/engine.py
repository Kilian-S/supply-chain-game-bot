# Core simulation engine for multi-region Supply Chain Game.
# Handles multiple factories, warehouses, routes, and fulfilment.

import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from ...network.config import NETWORK, ShippingMethod

from .demand import DemandLoader, GAME_START_DAY, GAME_END_DAY
from .build_schedule import BuildSchedule, DEFAULT_SCHEDULE
from .fulfilment import FulfilmentEngine, FulfilmentResult


# Financial constants (from network_config)
REVENUE_PER_DRUM = 1450
VARIABLE_COST_PER_DRUM = 900
FIXED_ORDER_COST = 2000
HOLDING_COST_PER_DRUM_PER_DAY = 0.25
TRUCK_CAPACITY = 200


@dataclass
class Shipment:
    """Inventory in transit from factory to warehouse."""
    quantity: int
    arrival_day: float
    source_factory: str
    destination_warehouse: str
    shipping_method: str


@dataclass
class WIPBatch:
    """Work in progress at a factory."""
    quantity: int
    completion_day: float
    destination_warehouse: str
    shipping_method: str


@dataclass
class RouteSettings:
    """Settings for a factory -> warehouse route."""
    reorder_point: int = 0
    order_quantity: int = 200
    shipping_method: str = "TRUCK"
    priority: int = 5


@dataclass
class FactoryState:
    """State of a single factory."""
    name: str
    wip: List[WIPBatch] = field(default_factory=list)
    factory_free_time: float = GAME_START_DAY

    # Settings per route (keyed by warehouse name)
    route_settings: Dict[str, RouteSettings] = field(default_factory=dict)

    @property
    def wip_total(self) -> int:
        return sum(w.quantity for w in self.wip)

    @property
    def is_busy(self) -> bool:
        return len(self.wip) > 0


@dataclass
class WarehouseState:
    """State of a single warehouse."""
    name: str
    inventory: int = 0
    in_transit: List[Shipment] = field(default_factory=list)

    @property
    def in_transit_total(self) -> int:
        return sum(s.quantity for s in self.in_transit)

    @property
    def in_transit_mail(self) -> int:
        return sum(s.quantity for s in self.in_transit if s.shipping_method == "MAIL")

    @property
    def in_transit_truck(self) -> int:
        return sum(s.quantity for s in self.in_transit if s.shipping_method == "TRUCK")

    @property
    def total_inventory(self) -> int:
        return self.inventory + self.in_transit_total


@dataclass
class DailyRecord:
    """Record of simulation state for one day."""
    day: int

    # Per-warehouse state
    warehouse_inventories: Dict[str, int] = field(default_factory=dict)
    warehouse_in_transit: Dict[str, int] = field(default_factory=dict)
    warehouse_wip: Dict[str, int] = field(default_factory=dict)  # WIP destined for each warehouse

    # Per-factory state
    factory_wip: Dict[str, int] = field(default_factory=dict)
    factory_capacities: Dict[str, int] = field(default_factory=dict)

    # Demand and fulfilment
    regional_demand: Dict[str, int] = field(default_factory=dict)
    fulfilment_results: Dict[str, FulfilmentResult] = field(default_factory=dict)

    # Aggregates
    total_demand: int = 0
    total_fulfilled: int = 0
    total_stockout: int = 0

    # Financials
    revenue: float = 0.0
    production_cost: float = 0.0
    fulfilment_cost: float = 0.0
    shipping_cost: float = 0.0
    truck_shipping_cost: float = 0.0
    mail_shipping_cost: float = 0.0
    holding_cost: float = 0.0
    interest: float = 0.0
    cash: float = 0.0

    # Shipments dispatched today: list of (factory, warehouse, quantity, method)
    shipments: List[Tuple[str, str, int, str]] = field(default_factory=list)

    # Production started today: list of (factory, warehouse, quantity, method)
    production_started: List[Tuple[str, str, int, str]] = field(default_factory=list)


class NetworkEngine:
    """
    Multi-region simulation engine.

    Simulates:
    - Multiple factories with independent WIP queues
    - Multiple warehouses with inventory and in-transit tracking
    - Cross-warehouse fulfilment with NEAREST policy
    - Configurable build schedules for factories/warehouses
    """

    def __init__(
        self,
        build_schedule: BuildSchedule = None,
        demand_folder: str = None,
        starting_cash: float = 20_000_000,
        annual_interest_rate: float = 0.10,
    ):
        """
        Initialise the network engine.

        Args:
            build_schedule: When factories/warehouses come online
            demand_folder: Path to demand Excel files
            starting_cash: Initial cash balance
            annual_interest_rate: Annual interest rate on cash
        """
        self.build_schedule = build_schedule or DEFAULT_SCHEDULE
        self.starting_cash = starting_cash
        self.annual_interest_rate = annual_interest_rate
        self.daily_interest_rate = (1 + annual_interest_rate) ** (1 / 365) - 1

        # Load demand data
        self.demand_loader = DemandLoader(demand_folder)
        self.demand_loader.load_all()

        # Initialize fulfilment engine
        self.fulfilment_engine = FulfilmentEngine()

        # Initialize state
        self._reset_state()

    def _reset_state(self):
        """Reset simulation to initial state."""
        self.current_day = GAME_START_DAY

        # Initialize factories
        self.factories: Dict[str, FactoryState] = {}
        for factory_name in NETWORK.factories:
            self.factories[factory_name] = FactoryState(name=factory_name)

            # Initialize route settings for each route from this factory
            for route in NETWORK.get_routes_from_factory(factory_name):
                self.factories[factory_name].route_settings[route.warehouse_name] = RouteSettings()

        # Initialize warehouses
        self.warehouses: Dict[str, WarehouseState] = {}
        for warehouse_name in NETWORK.warehouses:
            initial_inv = self.build_schedule.get_initial_inventory(warehouse_name)
            self.warehouses[warehouse_name] = WarehouseState(
                name=warehouse_name,
                inventory=initial_inv,
            )

        # Financial state
        build_costs = self.build_schedule.get_build_costs()
        self.total_capex = build_costs.get("total", 0)
        self.cash = self.starting_cash - self.total_capex

        # Cumulative tracking
        self.total_revenue = 0.0
        self.total_production_cost = 0.0
        self.total_fixed_production_cost = 0.0
        self.total_variable_production_cost = 0.0
        self.total_fulfilment_cost = 0.0
        self.total_shipping_cost = 0.0
        self.total_truck_shipping_cost = 0.0
        self.total_mail_shipping_cost = 0.0
        self.total_holding_cost = 0.0
        self.total_interest = 0.0
        self.total_stockouts = 0

        # History
        self.daily_records: List[DailyRecord] = []

        # Per-system operating modes (set by bot, displayed by visualizer)
        self.system_modes: Dict[str, str] = {
            'calopeia': '',
            'sorange': '',
            'fardo': '',
        }

    def reset(self):
        """Reset the simulation."""
        self._reset_state()

    @property
    def is_game_over(self) -> bool:
        return self.current_day > GAME_END_DAY

    def get_factory_capacity(self, factory: str) -> int:
        """Get current capacity for a factory."""
        return self.build_schedule.get_factory_capacity(factory, self.current_day)

    def is_warehouse_online(self, warehouse: str) -> bool:
        """Check if a warehouse is operational."""
        return self.build_schedule.is_warehouse_online(warehouse, self.current_day)

    def get_warehouse_inventory(self, warehouse: str) -> int:
        """Get current warehouse inventory."""
        if not self.is_warehouse_online(warehouse):
            return 0
        return self.warehouses[warehouse].inventory

    def get_warehouse_in_transit(self, warehouse: str) -> int:
        """Get in-transit inventory for a warehouse."""
        return self.warehouses[warehouse].in_transit_total

    def get_all_inventories(self) -> Dict[str, int]:
        """Get inventory for all online warehouses."""
        return {
            wh: self.warehouses[wh].inventory
            for wh in self.warehouses
            if self.is_warehouse_online(wh)
        }

    # === Settings Management ===

    def set_route_settings(
        self,
        factory: str,
        warehouse: str,
        rop: int = None,
        quantity: int = None,
        shipping_method: str = None,
        priority: int = None,
    ):
        """Set parameters for a factory -> warehouse route."""
        if factory not in self.factories:
            raise ValueError(f"Unknown factory: {factory}")

        settings = self.factories[factory].route_settings.get(warehouse)
        if settings is None:
            raise ValueError(f"No route from {factory} to {warehouse}")

        if rop is not None:
            settings.reorder_point = max(0, int(rop))
        if quantity is not None:
            settings.order_quantity = max(0, int(quantity))  # Allow 0 to disable production
        if shipping_method is not None:
            if shipping_method not in ("TRUCK", "MAIL"):
                raise ValueError(f"Invalid shipping method: {shipping_method}")
            settings.shipping_method = shipping_method
        if priority is not None:
            settings.priority = max(1, min(5, int(priority)))

    def apply_factory_settings(self, factory: str, routes: List[Dict]):
        """
        Apply settings for all routes from a factory.

        Args:
            factory: Factory name
            routes: List of dicts with keys: warehouse, shipping_method, order_point, quantity, priority
        """
        for route in routes:
            # Convert warehouse label to full name if needed
            warehouse = route['warehouse']
            if not warehouse.endswith('_WH'):
                warehouse = f"{warehouse}_WH"

            self.set_route_settings(
                factory=factory,
                warehouse=warehouse,
                rop=route.get('order_point'),
                quantity=route.get('quantity'),
                shipping_method=route.get('shipping_method', '').upper(),
                priority=route.get('priority'),
            )

    def set_system_mode(self, system: str, mode: str):
        """
        Set the operating mode for a system (for visualizer display).

        Args:
            system: System name ('calopeia', 'sorange', 'fardo')
            mode: Operating mode ('BUILD', 'CHASE', 'DRAWDOWN')
        """
        system_lower = system.lower()
        if system_lower in self.system_modes:
            self.system_modes[system_lower] = mode.upper() if mode else ''

    def get_system_mode(self, system: str) -> str:
        """Get the current operating mode for a system."""
        return self.system_modes.get(system.lower(), '')

    # === Simulation Step ===

    def step(self) -> DailyRecord:
        """
        Execute one day of simulation.

        Order of operations:
        1. Process arrivals (in_transit -> warehouse inventory)
        2. Get demand and fulfil (using NEAREST policy)
        3. Process WIP completion (wip -> in_transit)
        4. Check ROP triggers and start production
        5. Calculate financials
        6. Record state
        7. Advance day

        Returns:
            DailyRecord with today's state and metrics.
        """
        if self.is_game_over:
            raise RuntimeError("Simulation has ended")

        # 1. Process arrivals at all warehouses
        shipping_cost_today = 0.0
        truck_shipping_cost_today = 0.0
        mail_shipping_cost_today = 0.0
        for warehouse in self.warehouses.values():
            arrivals = [s for s in warehouse.in_transit if s.arrival_day < self.current_day + 1]
            for shipment in arrivals:
                warehouse.inventory += shipment.quantity
                warehouse.in_transit.remove(shipment)

        # 2. Get demand and fulfil
        regional_demand = self.demand_loader.get_all_demand_for_day(self.current_day)

        # Get current warehouse inventories (only online warehouses)
        warehouse_inventories = {
            wh: self.warehouses[wh].inventory
            for wh in self.warehouses
            if self.is_warehouse_online(wh)
        }

        # Fulfil demand
        fulfilment_results, updated_inventories = self.fulfilment_engine.fulfil_all_regions(
            regional_demand=regional_demand,
            warehouse_inventories=warehouse_inventories,
        )

        # Apply updated inventories back to warehouse states
        for wh, inv in updated_inventories.items():
            self.warehouses[wh].inventory = inv

        # Calculate fulfilment totals
        total_demand = sum(regional_demand.values())
        total_fulfilled = sum(r.fulfilled for r in fulfilment_results.values())
        total_stockout = sum(r.stockout for r in fulfilment_results.values())
        total_fulfilment_cost = sum(r.total_fulfilment_cost for r in fulfilment_results.values())

        self.total_stockouts += total_stockout

        # 3. Process WIP completion at all factories
        production_cost_today = 0.0
        shipments_today = []  # Track shipments for record

        for factory_name, factory in self.factories.items():
            capacity = self.get_factory_capacity(factory_name)
            if capacity == 0:
                continue  # Factory not built yet

            completed = [w for w in factory.wip if w.completion_day < self.current_day + 1]

            for batch in completed:
                factory.wip.remove(batch)
                factory.factory_free_time = batch.completion_day

                # Get shipping details from route
                route = NETWORK.get_route(factory_name, batch.destination_warehouse)
                method = ShippingMethod.MAIL if batch.shipping_method == "MAIL" else ShippingMethod.TRUCK

                if method == ShippingMethod.MAIL:
                    ship_time = route.shipping_options[method].lead_time_days
                    ship_cost = batch.quantity * route.shipping_options[method].cost_per_drum
                    mail_shipping_cost_today += ship_cost
                else:
                    ship_time = route.shipping_options[method].lead_time_days
                    num_trucks = int(np.ceil(batch.quantity / TRUCK_CAPACITY))
                    ship_cost = num_trucks * route.shipping_options[method].cost_per_truck
                    truck_shipping_cost_today += ship_cost

                shipping_cost_today += ship_cost

                # Track shipment
                shipments_today.append((factory_name, batch.destination_warehouse, batch.quantity, batch.shipping_method))

                # Add to warehouse in-transit
                self.warehouses[batch.destination_warehouse].in_transit.append(Shipment(
                    quantity=batch.quantity,
                    arrival_day=batch.completion_day + ship_time,
                    source_factory=factory_name,
                    destination_warehouse=batch.destination_warehouse,
                    shipping_method=batch.shipping_method,
                ))

        # 4. Check ROP triggers for all routes
        production_started_today = []  # Track production starts for record

        for factory_name, factory in self.factories.items():
            capacity = self.get_factory_capacity(factory_name)
            if capacity == 0 or factory.is_busy:
                continue

            # Check each route from this factory
            triggered_routes = []

            for warehouse_name, settings in factory.route_settings.items():
                if not self.is_warehouse_online(warehouse_name):
                    continue

                wh = self.warehouses[warehouse_name]
                total_inv = wh.inventory + wh.in_transit_total

                if total_inv <= settings.reorder_point and settings.order_quantity > 0:
                    triggered_routes.append((settings.priority, warehouse_name, settings))

            if triggered_routes:
                # Sort by priority (highest first)
                triggered_routes.sort(key=lambda x: -x[0])
                _, warehouse_name, settings = triggered_routes[0]

                # Start production
                production_time = settings.order_quantity / capacity
                start_time = max(factory.factory_free_time, float(self.current_day))

                factory.wip.append(WIPBatch(
                    quantity=settings.order_quantity,
                    completion_day=start_time + production_time,
                    destination_warehouse=warehouse_name,
                    shipping_method=settings.shipping_method,
                ))

                # Track production start
                production_started_today.append((factory_name, warehouse_name, settings.order_quantity, settings.shipping_method))

                # Production cost charged now (track fixed and variable separately)
                fixed_cost = FIXED_ORDER_COST
                variable_cost = settings.order_quantity * VARIABLE_COST_PER_DRUM
                production_cost_today += fixed_cost + variable_cost
                self.total_fixed_production_cost += fixed_cost
                self.total_variable_production_cost += variable_cost

        # 5. Calculate financials
        revenue = total_fulfilled * REVENUE_PER_DRUM

        # Holding cost for all warehouse inventory
        total_warehouse_inventory = sum(wh.inventory for wh in self.warehouses.values())
        holding_cost = total_warehouse_inventory * HOLDING_COST_PER_DRUM_PER_DAY

        self.total_revenue += revenue
        self.total_production_cost += production_cost_today
        self.total_fulfilment_cost += total_fulfilment_cost
        self.total_shipping_cost += shipping_cost_today
        self.total_truck_shipping_cost += truck_shipping_cost_today
        self.total_mail_shipping_cost += mail_shipping_cost_today
        self.total_holding_cost += holding_cost

        # Update cash and interest
        daily_pnl = revenue - production_cost_today - total_fulfilment_cost - shipping_cost_today - holding_cost
        self.cash += daily_pnl
        daily_interest = self.cash * self.daily_interest_rate
        self.cash += daily_interest
        self.total_interest += daily_interest

        # 6. Record state
        # Calculate WIP per destination warehouse (across all factories)
        warehouse_wip = {wh: 0 for wh in self.warehouses}
        for factory in self.factories.values():
            for batch in factory.wip:
                if batch.destination_warehouse in warehouse_wip:
                    warehouse_wip[batch.destination_warehouse] += batch.quantity

        record = DailyRecord(
            day=self.current_day,
            warehouse_inventories={wh: self.warehouses[wh].inventory for wh in self.warehouses},
            warehouse_in_transit={wh: self.warehouses[wh].in_transit_total for wh in self.warehouses},
            warehouse_wip=warehouse_wip,
            factory_wip={f: self.factories[f].wip_total for f in self.factories},
            factory_capacities={f: self.get_factory_capacity(f) for f in self.factories},
            regional_demand=regional_demand,
            fulfilment_results=fulfilment_results,
            total_demand=total_demand,
            total_fulfilled=total_fulfilled,
            total_stockout=total_stockout,
            revenue=revenue,
            production_cost=production_cost_today,
            fulfilment_cost=total_fulfilment_cost,
            shipping_cost=shipping_cost_today,
            truck_shipping_cost=truck_shipping_cost_today,
            mail_shipping_cost=mail_shipping_cost_today,
            holding_cost=holding_cost,
            interest=daily_interest,
            cash=self.cash,
            shipments=shipments_today,
            production_started=production_started_today,
        )
        self.daily_records.append(record)

        # 7. Advance day
        self.current_day += 1

        return record

    # === Financial Summaries ===

    @property
    def total_profit(self) -> float:
        """Total profit to date (includes capex and interest)."""
        return (
            self.total_revenue
            - self.total_production_cost
            - self.total_fulfilment_cost
            - self.total_shipping_cost
            - self.total_holding_cost
            - self.total_capex
            + self.total_interest
        )

    def get_financial_summary(self) -> Dict:
        """Get summary of financial metrics."""
        return {
            'total_revenue': self.total_revenue,
            'total_production_cost': self.total_production_cost,
            'total_fixed_production_cost': self.total_fixed_production_cost,
            'total_variable_production_cost': self.total_variable_production_cost,
            'total_fulfilment_cost': self.total_fulfilment_cost,
            'total_shipping_cost': self.total_shipping_cost,
            'total_truck_shipping_cost': self.total_truck_shipping_cost,
            'total_mail_shipping_cost': self.total_mail_shipping_cost,
            'total_holding_cost': self.total_holding_cost,
            'total_capex': self.total_capex,
            'total_interest': self.total_interest,
            'total_profit': self.total_profit,
            'cash': self.cash,
            'total_stockouts': self.total_stockouts,
        }

    def get_historical_demand_df(self):
        """Get historical demand as DataFrame (for bot interface)."""
        return self.demand_loader.get_historical_demand_df(self.current_day)
