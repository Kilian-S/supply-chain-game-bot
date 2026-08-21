# Configurable build schedule for factories and warehouses.
# Enables testing different strategies (early vs late builds).

from dataclasses import dataclass, field
from typing import Dict, Optional

# Build time constants (from network_config)
FACTORY_BUILD_DAYS = 90
WAREHOUSE_BUILD_DAYS = 60

# Game timing
GAME_START_DAY = 730
GAME_END_DAY = 1460


@dataclass
class BuildSchedule:
    """
    Configurable schedule for when factories and warehouses are ordered/built.

    Each entry specifies the day the build is ORDERED.
    Actual online day = order_day + build_time.

    For existing facilities (Calopeia_Factory, Calopeia_WH), order_day can be
    set to None or a day before GAME_START_DAY to indicate they exist from the start.

    Example:
        schedule = BuildSchedule(
            factory_orders={"Sorange_Factory": 730},  # Online day 820
            factory_capacities={"Sorange_Factory": 80},
        )
    """

    name: str = "default"

    # Day each factory expansion/build is ordered (None = exists from start)
    factory_orders: Dict[str, Optional[int]] = field(default_factory=lambda: {
        "Calopeia_Factory": None,  # Existing, capacity expansion ordered later
        "Sorange_Factory": 730,    # New factory
        "Fardo_Factory": 730,      # New factory
    })

    # Target capacity for each factory (after expansion/build completes)
    factory_capacities: Dict[str, int] = field(default_factory=lambda: {
        "Calopeia_Factory": 160,
        "Sorange_Factory": 80,
        "Fardo_Factory": 25,
    })

    # Starting capacity for existing factories (before expansion)
    factory_starting_capacities: Dict[str, int] = field(default_factory=lambda: {
        "Calopeia_Factory": 20,  # Starts with 20, expands to target
        "Sorange_Factory": 0,    # Doesn't exist until built
        "Fardo_Factory": 0,      # Doesn't exist until built
    })

    # Day capacity expansion is ordered for existing factories
    # (separate from factory_orders which is for new builds)
    capacity_expansion_orders: Dict[str, Optional[int]] = field(default_factory=lambda: {
        "Calopeia_Factory": 730,  # Order expansion day 730 → online day 820
    })

    # Day each warehouse is ordered (None = exists from start)
    warehouse_orders: Dict[str, Optional[int]] = field(default_factory=lambda: {
        "Calopeia_WH": None,  # Existing
        "Sorange_WH": 730,
        "Tyran_WH": 730,
        "Fardo_WH": 730,
    })

    # Initial inventory at existing warehouses
    initial_inventory: Dict[str, int] = field(default_factory=lambda: {
        "Calopeia_WH": 500,  # Starting inventory
    })

    def get_factory_online_day(self, factory: str) -> Optional[int]:
        """
        Get the day a factory comes online (or None if it exists from start).

        For new factories: order_day + FACTORY_BUILD_DAYS
        For existing factories with expansion: expansion comes online separately
        """
        order_day = self.factory_orders.get(factory)

        if order_day is None:
            return None  # Exists from start

        return order_day + FACTORY_BUILD_DAYS

    def get_capacity_expansion_day(self, factory: str) -> Optional[int]:
        """
        Get the day a factory's capacity expansion comes online.

        For existing factories that are expanding capacity.
        """
        order_day = self.capacity_expansion_orders.get(factory)

        if order_day is None:
            return None  # No expansion

        return order_day + FACTORY_BUILD_DAYS

    def get_warehouse_online_day(self, warehouse: str) -> Optional[int]:
        """
        Get the day a warehouse comes online (or None if it exists from start).
        """
        order_day = self.warehouse_orders.get(warehouse)

        if order_day is None:
            return None  # Exists from start

        return order_day + WAREHOUSE_BUILD_DAYS

    def get_factory_capacity(self, factory: str, day: int) -> int:
        """
        Get the factory's capacity on a given day.

        Accounts for:
        - Factory not yet built (returns 0)
        - Capacity expansion not yet complete (returns starting capacity)
        """
        # Check if factory exists yet
        online_day = self.get_factory_online_day(factory)
        if online_day is not None and day < online_day:
            return 0  # Factory not built yet

        # Check capacity level
        starting = self.factory_starting_capacities.get(factory, 0)
        target = self.factory_capacities.get(factory, starting)

        # For new factories, capacity is target once online
        if online_day is not None:
            return target

        # For existing factories, check expansion
        expansion_day = self.get_capacity_expansion_day(factory)
        if expansion_day is None or day >= expansion_day:
            return target
        else:
            return starting

    def is_warehouse_online(self, warehouse: str, day: int) -> bool:
        """Check if a warehouse is operational on a given day."""
        online_day = self.get_warehouse_online_day(warehouse)

        if online_day is None:
            return True  # Exists from start

        return day >= online_day

    def get_initial_inventory(self, warehouse: str) -> int:
        """Get initial inventory for a warehouse."""
        return self.initial_inventory.get(warehouse, 0)

    def get_build_costs(self) -> Dict[str, float]:
        """
        Calculate total build costs for this schedule.

        Returns dict with itemized costs.
        """
        from ...network.config import (
            FACTORY_BASE_COST,
            FACTORY_COST_PER_CAPACITY_UNIT,
            WAREHOUSE_BUILD_COST,
        )

        costs = {}

        # Factory costs
        for factory, order_day in self.factory_orders.items():
            if order_day is not None:
                # New factory build
                capacity = self.factory_capacities.get(factory, 0)
                cost = FACTORY_BASE_COST + capacity * FACTORY_COST_PER_CAPACITY_UNIT
                costs[f"{factory}_build"] = cost

        # Capacity expansion costs (existing factories)
        for factory, order_day in self.capacity_expansion_orders.items():
            if order_day is not None and factory not in [f for f, d in self.factory_orders.items() if d is not None]:
                starting = self.factory_starting_capacities.get(factory, 0)
                target = self.factory_capacities.get(factory, starting)
                added_capacity = target - starting
                if added_capacity > 0:
                    cost = added_capacity * FACTORY_COST_PER_CAPACITY_UNIT
                    costs[f"{factory}_expansion"] = cost

        # Warehouse costs
        for warehouse, order_day in self.warehouse_orders.items():
            if order_day is not None:
                costs[f"{warehouse}_build"] = WAREHOUSE_BUILD_COST

        costs["total"] = sum(v for k, v in costs.items() if k != "total")

        return costs

    def summary(self) -> str:
        """Return a human-readable summary of the build schedule."""
        lines = [f"Build Schedule: {self.name}", "=" * 40]

        lines.append("\nFactories:")
        for factory in self.factory_orders:
            online = self.get_factory_online_day(factory)
            capacity = self.factory_capacities.get(factory, 0)
            if online:
                lines.append(f"  {factory}: ordered → online day {online}, capacity {capacity}")
            else:
                expansion = self.get_capacity_expansion_day(factory)
                starting = self.factory_starting_capacities.get(factory, 0)
                if expansion:
                    lines.append(f"  {factory}: exists (cap {starting}), expansion → day {expansion} (cap {capacity})")
                else:
                    lines.append(f"  {factory}: exists, capacity {capacity}")

        lines.append("\nWarehouses:")
        for warehouse in self.warehouse_orders:
            online = self.get_warehouse_online_day(warehouse)
            if online:
                lines.append(f"  {warehouse}: ordered → online day {online}")
            else:
                inv = self.get_initial_inventory(warehouse)
                lines.append(f"  {warehouse}: exists, initial inventory {inv}")

        costs = self.get_build_costs()
        lines.append(f"\nTotal Build Cost: ${costs['total']:,.0f}")

        return "\n".join(lines)


# Pre-defined schedules for easy testing
DEFAULT_SCHEDULE = BuildSchedule(name="default")

EARLY_BUILD_SCHEDULE = BuildSchedule(
    name="early_build",
    factory_orders={
        "Calopeia_Factory": None,
        "Sorange_Factory": 730,
        "Fardo_Factory": 730,
    },
    warehouse_orders={
        "Calopeia_WH": None,
        "Sorange_WH": 730,
        "Tyran_WH": 730,
        "Fardo_WH": 730,
    },
)

LATE_SORANGE_SCHEDULE = BuildSchedule(
    name="late_sorange",
    factory_orders={
        "Calopeia_Factory": None,
        "Sorange_Factory": 1030,  # 300 days later
        "Fardo_Factory": 730,
    },
    warehouse_orders={
        "Calopeia_WH": None,
        "Sorange_WH": 1030,
        "Tyran_WH": 730,
        "Fardo_WH": 730,
    },
)
