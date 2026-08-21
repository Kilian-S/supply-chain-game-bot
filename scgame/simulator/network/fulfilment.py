# Fulfilment logic for multi-region demand.
# Implements NEAREST policy with configurable warehouse-region serving rules.

from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Set

from ...network.config import NETWORK


@dataclass
class FulfilmentResult:
    """Result of fulfilling demand for one region on one day."""
    region: str
    demand: int
    fulfilled: int
    stockout: int

    # Breakdown by source warehouse
    fulfilment_details: List[Dict] = field(default_factory=list)
    # Each entry: {'warehouse': str, 'quantity': int, 'cost_per_drum': float}

    @property
    def total_fulfilment_cost(self) -> float:
        return sum(d['quantity'] * d['cost_per_drum'] for d in self.fulfilment_details)


class FulfilmentEngine:
    """
    Handles demand fulfilment across warehouses using NEAREST policy.

    NEAREST policy:
    - For each demand region, rank warehouses by fulfilment cost (ascending)
    - Fulfil from lowest-cost warehouse first
    - If warehouse runs out, move to next-nearest
    - Only warehouses configured to serve the region can fulfil

    Warehouses can be configured to serve specific regions via can_fulfil_regions.
    """

    def __init__(self, can_fulfil_regions: Dict[str, Set[str]] = None):
        """
        Initialise the fulfilment engine.

        Args:
            can_fulfil_regions: Dict mapping warehouse name to set of regions it can serve.
                                If None, defaults to continental mutual backup, Fardo isolated.
        """
        if can_fulfil_regions is None:
            # Default: continental warehouses can backup each other, Fardo isolated
            continental_regions = {"Calopeia", "Sorange", "Tyran", "Entworpe"}
            self.can_fulfil_regions = {
                "Calopeia_WH": continental_regions,
                "Sorange_WH": continental_regions,
                "Tyran_WH": continental_regions,
                "Fardo_WH": {"Fardo"},
            }
        else:
            self.can_fulfil_regions = can_fulfil_regions

    def get_fulfilment_cost(self, warehouse: str, region: str) -> float:
        """Get the cost per drum to fulfil from warehouse to region."""
        return NETWORK.get_fulfilment_cost(warehouse, region)

    def get_warehouse_ranking(self, region: str, available_inventory: Dict[str, int]) -> List[Tuple[str, float]]:
        """
        Get warehouses ranked by fulfilment cost for a region (NEAREST policy).

        Args:
            region: Demand region
            available_inventory: Dict mapping warehouse name to available inventory

        Returns:
            List of (warehouse_name, cost_per_drum) sorted by cost ascending.
            Only includes warehouses that:
            1. Are configured to serve this region
            2. Have inventory > 0
        """
        candidates = []

        for warehouse, inventory in available_inventory.items():
            # Check if warehouse can serve this region
            allowed_regions = self.can_fulfil_regions.get(warehouse, set())
            if region not in allowed_regions:
                continue

            if inventory <= 0:
                continue

            cost = self.get_fulfilment_cost(warehouse, region)
            candidates.append((warehouse, cost, inventory))

        # Sort by cost (ascending), then by inventory (descending) as tiebreaker
        candidates.sort(key=lambda x: (x[1], -x[2]))

        return [(wh, cost) for wh, cost, _ in candidates]

    def fulfil_demand(
        self,
        region: str,
        quantity: int,
        warehouse_inventories: Dict[str, int],
    ) -> Tuple[FulfilmentResult, Dict[str, int]]:
        """
        Fulfil demand for a region using NEAREST policy.

        Args:
            region: Demand region
            quantity: Demand quantity
            warehouse_inventories: Dict mapping warehouse name to current inventory
                                  (will be modified in place to deduct fulfilled quantities)

        Returns:
            Tuple of:
            - FulfilmentResult with details
            - Updated warehouse_inventories dict (same object, modified)
        """
        if quantity <= 0:
            return FulfilmentResult(
                region=region,
                demand=0,
                fulfilled=0,
                stockout=0,
            ), warehouse_inventories

        remaining = quantity
        details = []

        # Get ranked warehouses
        ranking = self.get_warehouse_ranking(region, warehouse_inventories)

        for warehouse, cost_per_drum in ranking:
            if remaining <= 0:
                break

            available = warehouse_inventories[warehouse]
            fulfilled_qty = min(available, remaining)

            if fulfilled_qty > 0:
                # Deduct from inventory
                warehouse_inventories[warehouse] -= fulfilled_qty
                remaining -= fulfilled_qty

                details.append({
                    'warehouse': warehouse,
                    'quantity': fulfilled_qty,
                    'cost_per_drum': cost_per_drum,
                })

        result = FulfilmentResult(
            region=region,
            demand=quantity,
            fulfilled=quantity - remaining,
            stockout=remaining,
            fulfilment_details=details,
        )

        return result, warehouse_inventories

    def fulfil_all_regions(
        self,
        regional_demand: Dict[str, int],
        warehouse_inventories: Dict[str, int],
    ) -> Tuple[Dict[str, FulfilmentResult], Dict[str, int]]:
        """
        Fulfil demand for all regions.

        Args:
            regional_demand: Dict mapping region name to demand quantity
            warehouse_inventories: Dict mapping warehouse name to current inventory

        Returns:
            Tuple of:
            - Dict mapping region name to FulfilmentResult
            - Updated warehouse_inventories dict
        """
        results = {}

        # Process regions in a consistent order
        for region in sorted(regional_demand.keys()):
            demand = regional_demand[region]
            result, warehouse_inventories = self.fulfil_demand(
                region=region,
                quantity=demand,
                warehouse_inventories=warehouse_inventories,
            )
            results[region] = result

        return results, warehouse_inventories

    def analyse_fulfilment_costs(self) -> Dict[str, Dict[str, float]]:
        """
        Get a matrix of fulfilment costs for analysis.

        Returns:
            Dict[region][warehouse] = cost_per_drum (or None if not allowed)
        """
        regions = list(NETWORK.regions.keys())
        warehouses = list(NETWORK.warehouses.keys())

        matrix = {}
        for region in regions:
            matrix[region] = {}
            for warehouse in warehouses:
                allowed = self.can_fulfil_regions.get(warehouse, set())
                if region in allowed:
                    matrix[region][warehouse] = self.get_fulfilment_cost(warehouse, region)
                else:
                    matrix[region][warehouse] = None  # Not allowed

        return matrix
