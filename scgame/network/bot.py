"""Decision loop for the Network Run.

The Network Run splits five demand regions into three systems that never
exchange inventory. Each cycle reads the whole map once, then decides each
system independently:

  Calopeia system  One factory feeding the Calopeia and Tyran warehouses, with
                   Entworpe served from Calopeia by cross-region fulfilment.
  Sorange system   One factory feeding one warehouse.
  Fardo system     One factory feeding one warehouse on the island.

Every system follows the Always On policy. The reorder point is pinned above the
current pipeline so the factory never idles, and production stops only when the
stock already in hand covers all remaining demand. This replaces the three-mode
policy used in the Single-Region Run, because the regional forecasts here are too
noisy for mode switching to be reliable, and a factory left running is worth more
than a factory paused on a bad forecast.
"""

import time
import logging

from .config import NETWORK, GAME_END_DAY
from .calculator import (
    TYRAN_FLOOR,
    SHIPPING_TRUCK,
    forecast_warehouse_demand,
    calculate_calopeia_system_allocation,
    calculate_simple_system,
    calculate_days_of_supply,
)
from ..common.discord_logger import (
    send_embed,
    notify_startup,
    notify_error,
    notify_shutdown,
    notify_recovery,
    COLOUR_SUCCESS,
    COLOUR_WARNING,
    COLOUR_NEUTRAL,
)

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS = 300
MAX_CONSECUTIVE_ERRORS = 3

REGIONS = ("Calopeia", "Sorange", "Tyran", "Entworpe", "Fardo")

SYSTEM_WAREHOUSES = {
    "calopeia": ("Calopeia_WH", "Tyran_WH"),
    "sorange": ("Sorange_WH",),
    "fardo": ("Fardo_WH",),
}
SYSTEM_FACTORIES = {
    "calopeia": "Calopeia_Factory",
    "sorange": "Sorange_Factory",
    "fardo": "Fardo_Factory",
}


def notify_decision(summary: dict):
    """Post one cycle across all three systems to Discord.

    The embed is colour coded so the channel can be read at a glance. Green
    means every built system is still producing, amber means at least one has
    reached its shutdown condition, and grey means nothing is built yet.
    """
    lines = []
    demand = summary.get("today_demand", {})
    lines.append("**Demand today**")
    lines.append(
        " | ".join(f"{region[:3]} {demand.get(region, 0)}" for region in REGIONS)
    )
    lines.append("")

    warehouse_states = summary.get("warehouse_states", {})
    capacities = summary.get("capacities", {})
    systems = summary.get("systems", {})

    any_producing = False
    any_shutdown = False
    alerts = []

    for system_name, warehouses in SYSTEM_WAREHOUSES.items():
        decisions = systems.get(system_name, {})
        capacity = capacities.get(SYSTEM_FACTORIES[system_name], 0)

        if capacity == 0:
            lines.append(f"**{system_name.upper()}** not built")
        else:
            first = decisions.get(warehouses[0], {})
            mode = first.get("mode", "build")
            if mode == "drawdown":
                any_shutdown = True
            else:
                any_producing = True
            lines.append(
                f"**{system_name.upper()}** {mode.upper()}, capacity {capacity}/day"
            )

        for warehouse in warehouses:
            state = warehouse_states.get(warehouse, {})
            on_hand = state.get("inventory", 0)
            in_transit = state.get("mail", 0) + state.get("truck", 0)
            pipeline = on_hand + in_transit
            decision = decisions.get(warehouse, {})

            label = warehouse.replace("_WH", "")
            if capacity == 0:
                lines.append(f"  {label}: {pipeline:,} drums, waiting")
            else:
                lines.append(
                    f"  {label}: {on_hand:,} + {in_transit:,} = {pipeline:,} drums, "
                    f"reorder point {decision.get('reorder_point', 0):,}, "
                    f"priority {decision.get('priority', 0)}"
                )

            if warehouse == "Tyran_WH" and 0 < capacity and pipeline < TYRAN_FLOOR:
                alerts.append(f"Tyran below its floor, {pipeline} against {TYRAN_FLOOR}")

        lines.append("")

    if alerts:
        lines.append("**Alerts**")
        lines.extend(f"- {alert}" for alert in alerts)

    if any_shutdown:
        colour = COLOUR_WARNING
    elif any_producing:
        colour = COLOUR_SUCCESS
    else:
        colour = COLOUR_NEUTRAL

    send_embed(
        title=f"Day {summary.get('day', '?')}, cash ${summary.get('cash', 0):,.0f}",
        description="\n".join(lines),
        colour=colour,
    )


class NetworkBot:
    """Runs the Network Run strategy against the game or the simulator."""

    def __init__(self, game_controller, controller_factory=None):
        """
        Initialise the bot with a game controller.

        Args:
            game_controller: Instance of GameController for interacting with the game.
            controller_factory: Optional callable that returns a new GameController.
                               Enables automatic recovery from browser crashes.
        """
        self.controller = game_controller
        self._controller_factory = controller_factory
        self._consecutive_errors = 0
        self.last_processed_day = None

        # Per-cycle caches (invalidated each cycle)
        self._demand_df = None          # DataFrame: Day|Calopeia|Sorange|Tyran|Entworpe|Fardo
        self._warehouse_states = {}     # {wh_name: {inventory, mail, truck}}
        self._capacities = {}           # {factory_name: int}

    # ================================================================
    # MAIN CYCLE - Processes all systems
    # ================================================================

    def run_cycle(self, current_day: int) -> dict:
        """
        Execute a single-day decision cycle for ALL systems.

        Phase 1: Scrape all state (demand, inventories, capacities)
        Phase 2: Calculate decisions for each system
        Phase 3: Apply settings to each factory

        Returns:
            Dict with 'day' and 'systems' mapping system_name -> decisions
        """
        # ===== PHASE 1: SCRAPE ALL STATE =====
        logger.info(f"=== Day {current_day}: Starting cycle ===")

        # 1a. Scrape cash balance
        current_cash = self.controller.get_cash()

        # 1b. Scrape demand (all regions in one call)
        self._demand_df = self.controller.get_all_demand()
        logger.info(f"Demand scraped: {len(self._demand_df)} days of history")

        # Extract today's demand per region
        today_demand = {}
        today_row = self._demand_df[self._demand_df['Day'] == current_day]
        if not today_row.empty:
            for region in ['Calopeia', 'Sorange', 'Tyran', 'Entworpe', 'Fardo']:
                if region in today_row.columns:
                    today_demand[region] = int(today_row[region].iloc[0])
        logger.info(f"Today's demand: {today_demand}")

        # 1c. Scrape warehouse states (one call per warehouse)
        # Note: get_warehouse_state() returns zeros for under-construction warehouses
        self._warehouse_states = {}
        for wh_name in NETWORK.warehouses:
            state = self.controller.get_warehouse_state(wh_name)
            self._warehouse_states[wh_name] = state
            logger.info(f"{wh_name}: inv={state['inventory']}, "
                       f"mail={state['mail']}, truck={state['truck']}")

        # 1d. Scrape factory capacities
        self._capacities = {}
        for factory_name in NETWORK.factories:
            cap = self.controller.get_capacity(factory_name)
            self._capacities[factory_name] = cap
            logger.info(f"{factory_name}: capacity={cap}")

        # ===== PHASE 2: CALCULATE DECISIONS PER SYSTEM =====
        all_decisions = {}
        all_route_settings = {}  # {factory_name: [route_settings]}

        # Build regional demand dict for calculator functions
        regional_demand = self._build_regional_demand_dict()

        for system_name, system in NETWORK.systems.items():
            factory = system.factory_names[0]
            capacity = self._capacities[factory]

            if capacity == 0:
                logger.info(f"--- Pre-configuring {system_name} system (factory under construction) ---")
            else:
                logger.info(f"--- Processing {system_name} system ---")

            if system_name == "calopeia":
                decisions, route_settings = self._run_calopeia_system(
                    current_day=current_day,
                    regional_demand=regional_demand,
                )
            else:
                # Sorange or Fardo - simple single-warehouse system
                decisions, route_settings = self._run_simple_system(
                    current_day=current_day,
                    system=system,
                    regional_demand=regional_demand,
                )

            all_decisions[system_name] = decisions
            all_route_settings[factory] = route_settings

        # ===== PHASE 3: APPLY SETTINGS =====
        for factory_name, route_settings in all_route_settings.items():
            self.controller.apply_factory_settings(factory_name, route_settings)

        # ===== BUILD SUMMARY =====
        summary = {
            'day': current_day,
            'cash': current_cash,
            'today_demand': today_demand,
            'warehouse_states': self._warehouse_states,
            'capacities': self._capacities,
            'systems': all_decisions,
        }

        return summary

    # ================================================================
    # HELPER - Build regional demand dict from scraped DataFrame
    # ================================================================

    def _build_regional_demand_dict(self) -> dict:
        """
        Convert scraped demand DataFrame to regional demand dict for calculator.

        Returns:
            Dict mapping region name to DataFrame with 'day' and 'demand' columns.
        """
        regional_demand = {}
        for region in ['Calopeia', 'Sorange', 'Tyran', 'Entworpe', 'Fardo']:
            if region in self._demand_df.columns:
                region_df = self._demand_df[['Day', region]].copy()
                region_df = region_df.rename(columns={region: 'demand', 'Day': 'day'})
                regional_demand[region] = region_df
        return regional_demand

    # ================================================================
    # CALOPEIA SYSTEM - Multi-warehouse with Tyran floor
    # ================================================================

    def _run_calopeia_system(
        self,
        current_day: int,
        regional_demand: dict,
    ) -> tuple:
        """
        Process Calopeia system (factory serves Calopeia_WH and Tyran_WH).

        Strategy:
        - Tyran gets 300 drum floor (stable demand)
        - Everything else goes to Calopeia_WH (seasonal buffer building)
        - Shutdown when combined inventory covers remaining demand

        Returns:
            Tuple of (decisions_dict, route_settings_list)
        """
        factory = "Calopeia_Factory"
        capacity = self._capacities[factory]

        # Get warehouse states
        calopeia_state = self._warehouse_states["Calopeia_WH"]
        tyran_state = self._warehouse_states["Tyran_WH"]

        calopeia_inv = calopeia_state['inventory']
        calopeia_transit = calopeia_state['mail'] + calopeia_state['truck']
        tyran_inv = tyran_state['inventory']
        tyran_transit = tyran_state['mail'] + tyran_state['truck']

        # Calculate allocation using "Always On" strategy
        allocation = calculate_calopeia_system_allocation(
            calopeia_wh_inventory=calopeia_inv,
            calopeia_wh_in_transit=calopeia_transit,
            tyran_wh_inventory=tyran_inv,
            tyran_wh_in_transit=tyran_transit,
            factory_capacity=capacity,
            regional_historical_demand=regional_demand,
            current_day=current_day,
        )

        # Determine system mode (for visualizer)
        system_mode = allocation['calopeia_mode']
        if hasattr(self.controller, 'set_system_mode'):
            self.controller.set_system_mode("calopeia", system_mode.value.upper())

        # Calculate days of supply for logging
        calopeia_forecast = forecast_warehouse_demand(
            "Calopeia_WH", regional_demand, current_day, 30
        )
        tyran_forecast = forecast_warehouse_demand(
            "Tyran_WH", regional_demand, current_day, 30
        )

        calopeia_dos = calculate_days_of_supply(calopeia_inv, calopeia_transit, calopeia_forecast)
        tyran_dos = calculate_days_of_supply(tyran_inv, tyran_transit, tyran_forecast)

        # Log with priorities (Tyran gets priority 5 when below floor)
        tyran_pipeline = tyran_inv + tyran_transit
        logger.info(f"Calopeia_WH: ROP={allocation['calopeia_rop']}, pri={allocation['calopeia_priority']}, DoS={calopeia_dos:.1f}")
        logger.info(f"Tyran_WH: ROP={allocation['tyran_rop']}, pri={allocation['tyran_priority']}, pipeline={tyran_pipeline}")

        # Build decisions dict (priorities from allocation based on Tyran floor status)
        warehouse_decisions = {
            'Calopeia_WH': {
                'warehouse': 'Calopeia_WH',
                'factory': factory,
                'warehouse_inventory': calopeia_inv,
                'in_transit_inventory': calopeia_transit,
                'current_inventory': calopeia_inv + calopeia_transit,
                'capacity': capacity,
                'mode': allocation['calopeia_mode'].value,
                'shipping_method': SHIPPING_TRUCK,
                'order_quantity': allocation['calopeia_qty'],
                'reorder_point': allocation['calopeia_rop'],
                'days_of_supply': calopeia_dos,
                'priority': allocation['calopeia_priority'],
            },
            'Tyran_WH': {
                'warehouse': 'Tyran_WH',
                'factory': factory,
                'warehouse_inventory': tyran_inv,
                'in_transit_inventory': tyran_transit,
                'current_inventory': tyran_inv + tyran_transit,
                'capacity': capacity,
                'mode': allocation['tyran_mode'].value,
                'shipping_method': SHIPPING_TRUCK,
                'order_quantity': allocation['tyran_qty'],
                'reorder_point': allocation['tyran_rop'],
                'days_of_supply': tyran_dos,
                'priority': allocation['tyran_priority'],
            },
        }

        # Build route settings for controller (priorities from allocation)
        route_settings = [
            {
                'warehouse': 'Calopeia',
                'shipping_method': 'truck',
                'order_point': allocation['calopeia_rop'],
                'quantity': allocation['calopeia_qty'],
                'priority': allocation['calopeia_priority'],
            },
            {
                'warehouse': 'Tyran',
                'shipping_method': 'truck',
                'order_point': allocation['tyran_rop'],
                'quantity': allocation['tyran_qty'],
                'priority': allocation['tyran_priority'],
            },
        ]

        return warehouse_decisions, route_settings

    # ================================================================
    # SIMPLE SYSTEM - Sorange, Fardo (single warehouse)
    # ================================================================

    def _run_simple_system(
        self,
        current_day: int,
        system,
        regional_demand: dict,
    ) -> tuple:
        """
        Process a simple single-warehouse system (Sorange or Fardo).

        Strategy: Always On until shutdown trigger.

        Returns:
            Tuple of (decisions_dict, route_settings_list)
        """
        factory = system.factory_names[0]
        warehouse = system.warehouse_names[0]
        capacity = self._capacities[factory]

        # Get warehouse state
        wh_state = self._warehouse_states[warehouse]
        inventory = wh_state['inventory']
        in_transit = wh_state['mail'] + wh_state['truck']

        # Calculate settings using "Always On" strategy
        settings = calculate_simple_system(
            warehouse=warehouse,
            current_inventory=inventory,
            in_transit=in_transit,
            regional_historical_demand=regional_demand,
            current_day=current_day,
        )

        # Set system mode for visualizer
        if hasattr(self.controller, 'set_system_mode'):
            self.controller.set_system_mode(system.name, settings['mode'].value.upper())

        # Calculate days of supply for logging
        forecast = forecast_warehouse_demand(warehouse, regional_demand, current_day, 30)
        dos = calculate_days_of_supply(inventory, in_transit, forecast)

        logger.info(f"{warehouse}: ROP={settings['rop']}, mode={settings['mode'].value}, DoS={dos:.1f}")

        # Build decisions dict
        warehouse_decisions = {
            warehouse: {
                'warehouse': warehouse,
                'factory': factory,
                'warehouse_inventory': inventory,
                'in_transit_inventory': in_transit,
                'current_inventory': inventory + in_transit,
                'capacity': capacity,
                'mode': settings['mode'].value,
                'shipping_method': SHIPPING_TRUCK,
                'order_quantity': settings['qty'],
                'reorder_point': settings['rop'],
                'days_of_supply': dos,
                'priority': 5,
            },
        }

        # Build route settings for controller
        warehouse_label = warehouse.replace('_WH', '')
        route_settings = [
            {
                'warehouse': warehouse_label,
                'shipping_method': 'truck',
                'order_point': settings['rop'],
                'quantity': settings['qty'],
                'priority': 5,
            },
        ]

        return warehouse_decisions, route_settings

    # ================================================================
    # RECOVERY - Handle browser crashes
    # ================================================================

    def _recover(self):
        """
        Tear down the dead browser and create a fresh session.

        Returns True if recovery succeeded, False otherwise.
        """
        logger.info("Attempting recovery by recreating the browser session")

        attempt = self._consecutive_errors

        # Try to clean up the old driver
        try:
            self.controller.close()
        except Exception:
            pass

        # Create a fresh controller and log in
        try:
            self.controller = self._controller_factory()
            self.controller.login()
            current_day = self.controller.get_current_day()
            self._consecutive_errors = 0
            notify_recovery(current_day, attempt=attempt)
            logger.info(f"Recovery succeeded, resumed at day {current_day}")
            return True
        except Exception as e:
            logger.error(f"Recovery failed: {e}", exc_info=True)
            notify_error(f"Recovery failed: {e}", day=self.last_processed_day)
            return False

    # ================================================================
    # MAIN LOOP
    # ================================================================

    def run(self, poll_interval: int = DEFAULT_POLL_INTERVAL_SECONDS):
        """
        Main bot loop - lifecycle management.

        Continuously monitors the game and runs decision cycles when
        a new day is detected. Runs until game ends (day 1460) or
        manually interrupted.

        Args:
            poll_interval: Seconds between checks for day changes.
        """
        logger.info("Starting Multi-Region Supply Chain Bot (Always On Strategy)...")
        logger.info(f"Systems: {list(NETWORK.systems.keys())}")
        logger.info(f"Factories: {list(NETWORK.factories.keys())}")
        logger.info(f"Warehouses: {list(NETWORK.warehouses.keys())}")

        try:
            self.controller.login()
            logger.info("Logged in successfully")

            current_day = self.controller.get_current_day()
            notify_startup(current_day)

            # Main loop
            while True:
                try:
                    self.controller.refresh()
                    current_day = self.controller.get_current_day()

                    # Check if game has ended
                    if current_day > GAME_END_DAY:
                        logger.info(f"Game ended at day {current_day}")
                        notify_shutdown("Game ended", day=current_day)
                        break

                    # Check if this is a new day
                    if current_day != self.last_processed_day:
                        logger.info(f"New day detected: {current_day}")

                        decisions = self.run_cycle(current_day)
                        notify_decision(decisions)

                        self.last_processed_day = current_day

                    # Reset error counter on success
                    self._consecutive_errors = 0

                    # Wait before next check
                    time.sleep(poll_interval)

                except Exception as e:
                    self._consecutive_errors += 1
                    logger.error(f"Error in main loop ({self._consecutive_errors}/"
                                f"{MAX_CONSECUTIVE_ERRORS}): {e}", exc_info=True)
                    notify_error(
                        f"({self._consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}) {e}",
                        day=self.last_processed_day,
                    )

                    # If repeated failures, browser session is likely dead
                    if (self._consecutive_errors >= MAX_CONSECUTIVE_ERRORS
                            and self._controller_factory):
                        if self._recover():
                            continue  # Retry immediately with fresh session
                        # Recovery failed, so wait longer before the next attempt
                        time.sleep(poll_interval * 4)
                    else:
                        time.sleep(poll_interval * 2)

        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
            notify_shutdown("Stopped by user", day=self.last_processed_day)

        finally:
            logger.info("Bot shutdown complete")


# ================================================================
# ENTRY POINT
# ================================================================

if __name__ == "__main__":
    import sys

    from .controller import LiveNetworkController
    from ..common.discord_logger import DiscordHandler

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # Forwards every WARNING and above to Discord, if DISCORD_WEBHOOK_URL is set.
    logging.getLogger().addHandler(DiscordHandler())

    def make_controller():
        return LiveNetworkController(headless=True)

    bot = NetworkBot(make_controller(), controller_factory=make_controller)
    sys.exit(bot.run() or 0)
