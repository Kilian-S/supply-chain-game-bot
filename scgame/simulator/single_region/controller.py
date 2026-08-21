"""Simulator-backed controller for the Single-Region Run.

Presents the same interface as `scgame.single_region.controller`, so
`SingleRegionBot` cannot tell whether it is driving the live game or the engine.
That equivalence is the point of the simulator, because it means the strategy
exercised offline is the strategy that was played.
"""

import pandas as pd

from .engine import SingleRegionEngine


class SimulatedSingleRegionController:
    """Adapts `SingleRegionEngine` to the live controller interface."""

    def __init__(self, engine: SingleRegionEngine):
        self.engine = engine

    # --- Lifecycle ---

    def login(self):
        """No authentication is needed against the simulator."""

    def refresh(self):
        """Engine state is always current, so there is nothing to reload."""

    def close(self):
        """There is no browser session to release."""

    # --- Reading state ---

    def get_current_day(self) -> int:
        """Return the day the engine is about to simulate."""
        return self.engine.current_day

    def get_warehouse_inventory(self) -> int:
        """Return drums on the shelf and available to sell."""
        return self.engine.warehouse_inventory

    def get_in_transit_inventory(self) -> int:
        """Return drums dispatched but not yet received."""
        return self.engine.in_transit_total

    def get_capacity(self) -> int:
        """Return the factory's daily production capacity in drums."""
        return self.engine.capacity

    def get_historical_demand(self) -> pd.DataFrame:
        """Return demand observed up to today, with columns `day` and `demand`."""
        return self.engine.get_historical_demand()

    # --- Writing settings ---

    def set_reorder_point(self, reorder_point: int):
        """Set the level at or below which production is triggered."""
        self.engine.set_reorder_point(reorder_point)

    def set_order_quantity(self, quantity: int):
        """Set the batch size."""
        self.engine.set_order_quantity(quantity)

    def set_shipping_method(self, method: str):
        """Set the outbound shipping method."""
        self.engine.set_shipping_method(method)

    def apply_settings(self):
        """Settings reach the engine immediately, so there is nothing to flush."""

    # --- Diagnostics, beyond the live interface ---

    def set_diagnostics(self, mode: str, safety_stock: int, future_deficit: int):
        """Pass the bot's internal reasoning through for display and analysis."""
        self.engine.set_diagnostics(mode, safety_stock, future_deficit)
