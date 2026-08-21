"""Network topology and route economics for the Network Run.

The Network Run spans five demand regions across a continent and one island. The
strategy divides them into three systems that never exchange inventory, so that a
failure in one cannot starve another:

  Calopeia system  Calopeia factory supplies the Calopeia and Tyran warehouses.
                   Entworpe is served from the Calopeia warehouse by
                   cross-region fulfilment, because its demand arrives in
                   occasional 250-drum blocks that do not justify a warehouse of
                   its own.
  Sorange system   Sorange factory supplies the Sorange warehouse only.
  Fardo system     Fardo factory supplies the Fardo warehouse only. The island
                   is self-contained because shipping to it costs $45,000 per
                   truck against $15,000 within a region, which removes most of
                   the margin on any drum sent there from the mainland.

Every cost figure comes from `scgame.common.economics`, so this module describes
which routes exist and the written analysis and both simulators price them
identically.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple
from enum import Enum

from ..common.economics import (
    GAME_END_DAY,
    CAPACITY_BUILD_DAYS,
    WAREHOUSE_BUILD_DAYS,
    FIXED_COST_PER_BATCH,
    VARIABLE_COST_PER_DRUM,
    REVENUE_PER_DRUM,
    HOLDING_COST_PER_DRUM_PER_DAY,
    STOCKOUT_COST_PER_DRUM,
    TRUCK_CAPACITY,
    TRUCK_COST_SAME_REGION,
    TRUCK_COST_CROSS_REGION,
    TRUCK_COST_TO_FARDO,
    MAIL_COST_SAME_REGION,
    MAIL_COST_CROSS_REGION,
    MAIL_COST_TO_FARDO,
    SHIPPING_DAYS_TRUCK,
    SHIPPING_DAYS_TRUCK_TO_FARDO,
    SHIPPING_DAYS_MAIL,
    SHIPPING_DAYS_MAIL_TO_FARDO,
    FULFILMENT_COST_SAME_REGION,
    FULFILMENT_COST_CROSS_REGION,
    FULFILMENT_COST_TO_FARDO,
    FACTORY_BASE_COST,
    FACTORY_COST_PER_CAPACITY_UNIT,
    WAREHOUSE_BUILD_COST,
    ANNUAL_INTEREST_RATE,
)

# Re-exported so that importers of this module get the topology and its
# pricing from one place. The authoritative definitions live in
# scgame.common.economics.
__all__ = [
    "NETWORK", "ShippingMethod", "ShippingOption", "Region", "Factory",
    "Warehouse", "Route", "System", "NetworkConfig",
    "GAME_END_DAY", "CAPACITY_BUILD_DAYS", "WAREHOUSE_BUILD_DAYS",
    "FIXED_COST_PER_BATCH", "VARIABLE_COST_PER_DRUM", "REVENUE_PER_DRUM",
    "HOLDING_COST_PER_DRUM_PER_DAY", "STOCKOUT_COST_PER_DRUM",
    "TRUCK_CAPACITY", "FACTORY_BASE_COST", "FACTORY_COST_PER_CAPACITY_UNIT",
    "WAREHOUSE_BUILD_COST", "ANNUAL_INTEREST_RATE", "DEFAULT_STARTING_CASH",
    "CALOPEIA", "SORANGE", "TYRAN", "ENTWORPE", "FARDO",
    "CONTINENTAL_REGION_IDS",
]

# Region identifiers. Fardo is the island, and is deliberately the highest
# number so that a comparison against 4 distinguishes continent from island.
CALOPEIA, SORANGE, TYRAN, ENTWORPE, FARDO = 1, 2, 3, 4, 5
CONTINENTAL_REGION_IDS = (CALOPEIA, SORANGE, TYRAN, ENTWORPE)

DEFAULT_STARTING_CASH = 2_000_000.0


# ============================================================
# ENUMS
# ============================================================

class ShippingMethod(Enum):
    TRUCK = "truck"
    MAIL = "mail"


# ============================================================
# DATA CLASSES
# ============================================================

@dataclass(frozen=True)
class ShippingOption:
    """Shipping characteristics for a specific method on a route."""
    method: ShippingMethod
    lead_time_days: int
    cost_per_drum: float      # For MAIL (0 for TRUCK)
    cost_per_truck: float     # For TRUCK (0 for MAIL)
    truck_capacity: int = TRUCK_CAPACITY


@dataclass(frozen=True)
class Region:
    """A demand region (customer location)."""
    name: str
    region_id: int  # 1=Calopeia, 2=Sorange, 3=Tyran, 4=Entworpe, 5=Fardo


@dataclass(frozen=True)
class Factory:
    """A production facility."""
    name: str
    region_id: int
    icon_src: str
    is_new_build: bool = False  # True if built during game (incurs build cost)


@dataclass(frozen=True)
class Warehouse:
    """A storage/distribution facility."""
    name: str
    region_id: int
    icon_src: str
    serves_regions: Tuple[str, ...]  # Demand regions this WH fulfils
    is_new_build: bool = False


@dataclass(frozen=True)
class Route:
    """A factory -> warehouse supply route with shipping options."""
    factory_name: str
    warehouse_name: str
    shipping_options: Dict[ShippingMethod, ShippingOption]
    default_shipping: ShippingMethod = ShippingMethod.TRUCK


@dataclass(frozen=True)
class System:
    """An independent operating system (no inventory flows between systems)."""
    name: str
    factory_names: Tuple[str, ...]
    warehouse_names: Tuple[str, ...]
    region_names: Tuple[str, ...]


# ============================================================
# NETWORK CONFIG CLASS
# ============================================================

@dataclass
class NetworkConfig:
    """
    Complete network topology with lookup methods.

    Usage:
        from scgame.network.config import NETWORK, ShippingMethod

        route = NETWORK.get_route("Calopeia_Factory", "Tyran_WH")
        lead_time = NETWORK.get_lead_time("Calopeia_Factory", "Tyran_WH", ShippingMethod.TRUCK)
    """
    regions: Dict[str, Region] = field(default_factory=dict)
    factories: Dict[str, Factory] = field(default_factory=dict)
    warehouses: Dict[str, Warehouse] = field(default_factory=dict)
    routes: Dict[Tuple[str, str], Route] = field(default_factory=dict)
    systems: Dict[str, System] = field(default_factory=dict)
    # Shipping between the continent and the island. No route uses these,
    # because the island runs as a closed system, but they are the prices
    # that decision was made against.
    intercontinental_options: Dict[ShippingMethod, ShippingOption] = field(
        default_factory=dict
    )

    # === Route Lookups ===

    def get_route(self, factory: str, warehouse: str) -> Route:
        """Get route by factory and warehouse names."""
        return self.routes[(factory, warehouse)]

    def get_routes_from_factory(self, factory: str) -> List[Route]:
        """Get all routes originating from a factory."""
        return [r for (f, _), r in self.routes.items() if f == factory]

    def get_routes_to_warehouse(self, warehouse: str) -> List[Route]:
        """Get all routes supplying a warehouse."""
        return [r for (_, w), r in self.routes.items() if w == warehouse]

    # === Lead Time ===

    def get_lead_time(self, factory: str, warehouse: str, method: ShippingMethod) -> int:
        """Get shipping lead time in days for a route and method."""
        route = self.get_route(factory, warehouse)
        return route.shipping_options[method].lead_time_days

    # === Shipping Cost ===

    def get_shipping_cost(
        self, factory: str, warehouse: str, method: ShippingMethod, quantity: int
    ) -> float:
        """Calculate shipping cost for given quantity."""
        route = self.get_route(factory, warehouse)
        option = route.shipping_options[method]

        if method == ShippingMethod.MAIL:
            return quantity * option.cost_per_drum
        else:  # TRUCK
            num_trucks = -(-quantity // option.truck_capacity)  # Ceiling division
            return num_trucks * option.cost_per_truck

    # === Fulfilment Cost ===

    def get_fulfilment_cost(self, warehouse: str, customer_region: str) -> float:
        """Return the cost per drum to serve a customer region from a warehouse.

        Fulfilment is priced by how far the drum has to travel from the
        warehouse holding it to the customer. Serving a customer in the same
        region as the warehouse is cheapest. Serving another continental region
        costs more, and anything crossing between the continent and the island
        costs most of all.
        """
        warehouse_region = self.warehouses[warehouse].region_id
        customer = self.regions[customer_region].region_id

        if warehouse_region == customer:
            return FULFILMENT_COST_SAME_REGION
        if warehouse_region in CONTINENTAL_REGION_IDS and customer in CONTINENTAL_REGION_IDS:
            return FULFILMENT_COST_CROSS_REGION
        return FULFILMENT_COST_TO_FARDO

    # === Region/Warehouse Lookups ===

    def get_regions_served_by(self, warehouse: str) -> List[str]:
        """Get list of demand regions served by a warehouse."""
        return list(self.warehouses[warehouse].serves_regions)

    def get_warehouse_for_region(self, region: str) -> str:
        """Find which warehouse serves a given demand region."""
        for wh in self.warehouses.values():
            if region in wh.serves_regions:
                return wh.name
        raise KeyError(f"No warehouse serves region {region}")

    # === System Lookups ===

    def get_system_for_factory(self, factory: str) -> System:
        """Get the system a factory belongs to."""
        for sys in self.systems.values():
            if factory in sys.factory_names:
                return sys
        raise KeyError(f"Factory {factory} not in any system")

    def get_system_for_warehouse(self, warehouse: str) -> System:
        """Get the system a warehouse belongs to."""
        for sys in self.systems.values():
            if warehouse in sys.warehouse_names:
                return sys
        raise KeyError(f"Warehouse {warehouse} not in any system")

    def get_system(self, name: str) -> System:
        """Get system by name."""
        return self.systems[name]


# ============================================================
# BUILD CONFIGURATION
# ============================================================

def _build_config() -> NetworkConfig:
    """
    Build the multi-region network configuration.

    Strategy (3-system approach):
    - Calopeia system: Calopeia Factory serves Calopeia WH and Tyran WH
    - Sorange system: Sorange Factory serves Sorange WH (closed system)
    - Fardo system: Fardo Factory serves Fardo WH (closed system)
    - Entworpe demand served from Calopeia WH (no Entworpe warehouse)
    """

    # --- Regions ---
    regions = {
        "Calopeia": Region("Calopeia", region_id=1),
        "Sorange": Region("Sorange", region_id=2),
        "Tyran": Region("Tyran", region_id=3),
        "Entworpe": Region("Entworpe", region_id=4),
        "Fardo": Region("Fardo", region_id=5),
    }

    # --- Factories ---
    factories = {
        "Calopeia_Factory": Factory(
            name="Calopeia_Factory",
            region_id=1,
            icon_src="factory1.gif",
            is_new_build=False,  # Existing factory, just expanding capacity
        ),
        "Sorange_Factory": Factory(
            name="Sorange_Factory",
            region_id=2,
            icon_src="factory2.gif",
            is_new_build=True,  # Built on day 1
        ),
        "Fardo_Factory": Factory(
            name="Fardo_Factory",
            region_id=5,
            icon_src="factory5.gif",
            is_new_build=True,  # Built on day 1
        ),
    }

    # --- Warehouses ---
    warehouses = {
        "Calopeia_WH": Warehouse(
            name="Calopeia_WH",
            region_id=1,
            icon_src="warehouse1.gif",
            serves_regions=("Calopeia", "Entworpe"),  # Serves 2 regions
            is_new_build=False,  # Existing
        ),
        "Sorange_WH": Warehouse(
            name="Sorange_WH",
            region_id=2,
            icon_src="warehouse2.gif",
            serves_regions=("Sorange",),
            is_new_build=True,
        ),
        "Tyran_WH": Warehouse(
            name="Tyran_WH",
            region_id=3,
            icon_src="warehouse3.gif",
            serves_regions=("Tyran",),
            is_new_build=True,
        ),
        "Fardo_WH": Warehouse(
            name="Fardo_WH",
            region_id=5,
            icon_src="warehouse5.gif",
            serves_regions=("Fardo",),
            is_new_build=True,
        ),
    }

    # --- Shipping Options ---

    # Same region: $15k truck (7 days), $150/drum mail (1 day)
    same_region_truck = ShippingOption(
        method=ShippingMethod.TRUCK,
        lead_time_days=SHIPPING_DAYS_TRUCK,
        cost_per_drum=0,
        cost_per_truck=TRUCK_COST_SAME_REGION,
    )
    same_region_mail = ShippingOption(
        method=ShippingMethod.MAIL,
        lead_time_days=SHIPPING_DAYS_MAIL,
        cost_per_drum=MAIL_COST_SAME_REGION,
        cost_per_truck=0,
    )

    # Different continental regions: $20k truck (7 days), $200/drum mail (1 day)
    diff_continent_truck = ShippingOption(
        method=ShippingMethod.TRUCK,
        lead_time_days=SHIPPING_DAYS_TRUCK,
        cost_per_drum=0,
        cost_per_truck=TRUCK_COST_CROSS_REGION,
    )
    diff_continent_mail = ShippingOption(
        method=ShippingMethod.MAIL,
        lead_time_days=SHIPPING_DAYS_MAIL,
        cost_per_drum=MAIL_COST_CROSS_REGION,
        cost_per_truck=0,
    )

    # Continent to Fardo and back. No route below uses these, because the
    # island runs as a closed system, but they are defined so that the cost
    # of the option the strategy rejected stays visible and testable.
    intercontinental_truck = ShippingOption(
        method=ShippingMethod.TRUCK,
        lead_time_days=SHIPPING_DAYS_TRUCK_TO_FARDO,
        cost_per_drum=0,
        cost_per_truck=TRUCK_COST_TO_FARDO,
    )
    intercontinental_mail = ShippingOption(
        method=ShippingMethod.MAIL,
        lead_time_days=SHIPPING_DAYS_MAIL_TO_FARDO,
        cost_per_drum=MAIL_COST_TO_FARDO,
        cost_per_truck=0,
    )

    # --- Routes ---
    routes = {
        # Calopeia system: Calopeia Factory -> Calopeia WH + Tyran WH
        ("Calopeia_Factory", "Calopeia_WH"): Route(
            factory_name="Calopeia_Factory",
            warehouse_name="Calopeia_WH",
            shipping_options={
                ShippingMethod.TRUCK: same_region_truck,
                ShippingMethod.MAIL: same_region_mail,
            },
            default_shipping=ShippingMethod.TRUCK,
        ),
        # Sorange system: Sorange Factory -> Sorange WH (same region)
        ("Sorange_Factory", "Sorange_WH"): Route(
            factory_name="Sorange_Factory",
            warehouse_name="Sorange_WH",
            shipping_options={
                ShippingMethod.TRUCK: same_region_truck,
                ShippingMethod.MAIL: same_region_mail,
            },
            default_shipping=ShippingMethod.TRUCK,
        ),
        ("Calopeia_Factory", "Tyran_WH"): Route(
            factory_name="Calopeia_Factory",
            warehouse_name="Tyran_WH",
            shipping_options={
                ShippingMethod.TRUCK: diff_continent_truck,
                ShippingMethod.MAIL: diff_continent_mail,
            },
            default_shipping=ShippingMethod.TRUCK,
        ),

        # Fardo system: Fardo Factory -> Fardo WH (same region)
        ("Fardo_Factory", "Fardo_WH"): Route(
            factory_name="Fardo_Factory",
            warehouse_name="Fardo_WH",
            shipping_options={
                ShippingMethod.TRUCK: same_region_truck,
                ShippingMethod.MAIL: same_region_mail,
            },
            default_shipping=ShippingMethod.TRUCK,
        ),
    }

    # --- Systems (independent operating units) ---
    systems = {
        "calopeia": System(
            name="calopeia",
            factory_names=("Calopeia_Factory",),
            warehouse_names=("Calopeia_WH", "Tyran_WH"),
            region_names=("Calopeia", "Tyran", "Entworpe"),
        ),
        "sorange": System(
            name="sorange",
            factory_names=("Sorange_Factory",),
            warehouse_names=("Sorange_WH",),
            region_names=("Sorange",),
        ),
        "fardo": System(
            name="fardo",
            factory_names=("Fardo_Factory",),
            warehouse_names=("Fardo_WH",),
            region_names=("Fardo",),
        ),
    }

    # Recorded so that the cost of supplying the island from the mainland,
    # the option the strategy rejected, stays visible and testable.
    rejected_intercontinental = {
        ShippingMethod.TRUCK: intercontinental_truck,
        ShippingMethod.MAIL: intercontinental_mail,
    }

    return NetworkConfig(
        intercontinental_options=rejected_intercontinental,
        regions=regions,
        factories=factories,
        warehouses=warehouses,
        routes=routes,
        systems=systems,
    )


# ============================================================
# SINGLETON - Import this in other modules
# ============================================================

NETWORK = _build_config()
