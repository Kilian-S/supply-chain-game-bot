# Simulated GameController for multi-region bot testing.
# Implements the same interface as the real GameController.

import pandas as pd
from typing import Dict, List


from .engine import NetworkEngine
from .build_schedule import BuildSchedule


class SimulatedNetworkController:
    """
    Simulated game controller for testing the multi-region bot.

    Implements the same interface as the real GameController,
    allowing the bot to work with either without code changes.

    Interface methods:
    - login()
    - get_current_day()
    - get_all_demand()
    - get_warehouse_state(warehouse)
    - get_capacity(factory)
    - apply_factory_settings(factory, routes)
    """

    def __init__(self, engine: NetworkEngine = None, build_schedule: BuildSchedule = None):
        """
        Initialise with a network engine.

        Args:
            engine: NetworkEngine instance. If None, creates one with default settings.
            build_schedule: Build schedule to use if creating new engine.
        """
        if engine is None:
            engine = NetworkEngine(build_schedule=build_schedule)

        self.engine = engine
        self._logged_in = False

    # === Lifecycle ===

    def refresh(self):
        """Engine state is always current, so there is nothing to reload."""

    def login(self):
        """Simulate login (no-op for simulator)."""
        self._logged_in = True

    def close(self):
        """Simulate closing (no-op for simulator)."""
        pass

    # === Getters (read game state) ===

    def get_current_day(self) -> int:
        """Get the current simulation day."""
        return self.engine.current_day

    def get_all_demand(self) -> pd.DataFrame:
        """
        Get historical demand for all regions.

        Returns:
            DataFrame with columns: Day, Calopeia, Sorange, Tyran, Entworpe, Fardo
        """
        return self.engine.get_historical_demand_df()

    def get_warehouse_state(self, warehouse: str) -> Dict:
        """
        Get inventory state for a specific warehouse.

        Args:
            warehouse: Warehouse name (e.g., 'Calopeia_WH')

        Returns:
            Dict with keys: inventory, mail, truck
        """
        if not self.engine.is_warehouse_online(warehouse):
            return {'inventory': 0, 'mail': 0, 'truck': 0}

        wh = self.engine.warehouses[warehouse]
        return {
            'inventory': wh.inventory,
            'mail': wh.in_transit_mail,
            'truck': wh.in_transit_truck,
        }

    def get_capacity(self, factory: str) -> int:
        """
        Get current capacity for a factory.

        Args:
            factory: Factory name (e.g., 'Calopeia_Factory')

        Returns:
            Current capacity (0 if not yet built)
        """
        return self.engine.get_factory_capacity(factory)

    def get_cash(self) -> float:
        """
        Get current cash balance.

        Returns:
            Current cash as float
        """
        return self.engine.cash

    # === Setters ===

    def apply_factory_settings(self, factory: str, routes: List[Dict]):
        """
        Apply settings for all routes from a factory.

        Args:
            factory: Factory name
            routes: List of dicts with keys:
                - warehouse: str (e.g., 'Calopeia' or 'Calopeia_WH')
                - shipping_method: str ('truck' or 'mail')
                - order_point: int
                - quantity: int
                - priority: int
        """
        self.engine.apply_factory_settings(factory, routes)

    def set_system_mode(self, system: str, mode: str):
        """
        Set the operating mode for a system (for visualizer display).

        Args:
            system: System name ('calopeia', 'sorange', 'fardo')
            mode: Operating mode ('BUILD', 'CHASE', 'DRAWDOWN')
        """
        self.engine.set_system_mode(system, mode)

    # === Additional methods for simulation ===

    def step(self):
        """
        Advance the simulation by one day.

        This is called by the simulation runner, not by the bot.
        The bot just reads state and applies settings.
        """
        return self.engine.step()

    def get_financial_summary(self) -> Dict:
        """Get financial summary from the engine."""
        return self.engine.get_financial_summary()

    @property
    def is_game_over(self) -> bool:
        """Check if simulation has ended."""
        return self.engine.is_game_over


# Legacy single-region interface (for backwards compatibility with old bot)
