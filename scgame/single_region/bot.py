"""Decision loop for the Single-Region Run.

The bot polls the game, detects when the game day has advanced, and runs exactly
one decision cycle per game day. A cycle reads the state of the world, forecasts
demand, selects an operating mode, converts that mode into a reorder point, an
order quantity, and a shipping method, and writes those three settings back.

The same class drives both the live game and the offline simulator. Both are
given a controller exposing the same interface, so the strategy code that ran
during the assessed game is the strategy code the simulator exercises.
"""

import csv
import time
import logging
from pathlib import Path

from ..common.economics import GAME_END_DAY
from ..common.discord_logger import (
    send_embed,
    notify_startup,
    notify_error,
    notify_shutdown,
    notify_recovery,
    COLOUR_INFO,
    COLOUR_SUCCESS,
    COLOUR_WARNING,
)
from .calculator import (
    STANDARD_ORDER_QUANTITY,
    SHIPPING_TRUCK,
    SHIPPING_MAIL,
    OperatingMode,
    calculate_effective_lead_time,
    forecast_demand,
    calculate_demand_statistics,
    calculate_future_deficit,
    select_operating_mode,
    is_stockout_imminent,
    calculate_order_quantity,
    calculate_safety_stock,
    calculate_reorder_point,
    FUTURE_DEFICIT_HORIZON_DAYS,
)

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS = 300
FORECAST_HORIZON_DAYS = 365
MAX_CONSECUTIVE_ERRORS = 3

DECISION_LOG_COLUMNS = [
    "day",
    "mode",
    "warehouse_inventory",
    "in_transit_inventory",
    "current_inventory",
    "capacity",
    "demand_mean",
    "demand_std",
    "future_deficit",
    "stockout_imminent",
    "shipping_method",
    "order_quantity",
    "lead_time",
    "safety_stock",
    "reorder_point",
]


def notify_decision(decisions: dict):
    """Post one cycle's decisions to Discord."""
    mode = decisions.get("mode", "unknown")
    colour = {
        "build": COLOUR_INFO,
        "chase": COLOUR_SUCCESS,
        "drawdown": COLOUR_WARNING,
    }.get(mode, COLOUR_INFO)

    fields = {
        "Mode": mode.upper(),
        "Warehouse": decisions.get("warehouse_inventory", "?"),
        "In transit": decisions.get("in_transit_inventory", "?"),
        "Total inventory": decisions.get("current_inventory", "?"),
        "Capacity": decisions.get("capacity", "?"),
        "Reorder point": decisions.get("reorder_point", "?"),
        "Order quantity": decisions.get("order_quantity", "?"),
        "Shipping": decisions.get("shipping_method", "?"),
        "Safety stock": decisions.get("safety_stock", "?"),
    }
    if decisions.get("stockout_imminent"):
        fields["Stockout"] = "Imminent"

    send_embed(
        title=f"Day {decisions.get('day', '?')} decision", fields=fields, colour=colour
    )


class SingleRegionBot:
    """Runs the Single-Region Run strategy against a game or a simulator."""

    def __init__(self, game_controller, controller_factory=None):
        """
        Args:
            game_controller: Object exposing the controller interface, either
                `LiveSingleRegionController` or `SimulatedController`.
            controller_factory: Optional zero-argument callable returning a new
                controller. Supplying it enables recovery from browser crashes,
                which is what allowed the live bot to run unattended.
        """
        self.controller = game_controller
        self._controller_factory = controller_factory
        self._consecutive_errors = 0
        self.last_processed_day = None
        self.decision_log = []

    # ------------------------------------------------------------------
    # One game day
    # ------------------------------------------------------------------

    def run_cycle(self, current_day: int) -> dict:
        """Run one day's decision cycle and apply the result to the game.

        The day is passed in rather than read again here, so that the decisions
        recorded against a day are guaranteed to be the decisions taken for that
        day even if the game clock advances mid-cycle.
        """
        warehouse_inventory = self.controller.get_warehouse_inventory()
        in_transit_inventory = self.controller.get_in_transit_inventory()
        capacity = self.controller.get_capacity()
        historical_demand = self.controller.get_historical_demand()
        current_inventory = warehouse_inventory + in_transit_inventory

        logger.info(
            "Day %d: warehouse=%d, in transit=%d, capacity=%d",
            current_day, warehouse_inventory, in_transit_inventory, capacity,
        )

        forecast = forecast_demand(
            historical_demand=historical_demand,
            current_day=current_day,
            horizon_days=FORECAST_HORIZON_DAYS,
        )

        statistics = calculate_demand_statistics(historical_demand)
        demand_std = statistics["std"]

        logger.info(
            "Day %d: demand mean=%.1f, std=%.1f, cv=%.2f",
            current_day, statistics["mean"], statistics["std"], statistics["cv"],
        )

        future_deficit = calculate_future_deficit(
            forecast_demand=forecast[:FUTURE_DEFICIT_HORIZON_DAYS],
            capacity=capacity,
        )

        # Mode selection needs a safety stock figure, and safety stock needs a
        # lead time, so a provisional lead time based on the standard batch is
        # used here and the final figure is recomputed below once the actual
        # order quantity and shipping method are known.
        provisional_lead_time = calculate_effective_lead_time(
            order_quantity=STANDARD_ORDER_QUANTITY,
            capacity=capacity,
            shipping_method=SHIPPING_TRUCK,
        )
        provisional_safety_stock = calculate_safety_stock(
            demand_std=demand_std, lead_time=provisional_lead_time
        )

        mode = select_operating_mode(
            current_day=current_day,
            forecast_demand=forecast,
            capacity=capacity,
            current_inventory=current_inventory,
            future_deficit=future_deficit,
            safety_stock=provisional_safety_stock,
        )

        logger.info(
            "Day %d: mode=%s, future deficit=%d",
            current_day, mode.value, future_deficit,
        )

        stockout_imminent = is_stockout_imminent(
            warehouse_inventory=warehouse_inventory,
            in_transit_inventory=in_transit_inventory,
            forecast_demand=forecast,
            capacity=capacity,
            order_quantity=STANDARD_ORDER_QUANTITY,
        )
        if stockout_imminent:
            logger.warning("Day %d: stockout imminent", current_day)

        order_quantity, order_shipping_method = calculate_order_quantity(
            current_day=current_day,
            current_inventory=current_inventory,
            forecast_demand=forecast,
            capacity=capacity,
        )

        # Mail is worth its premium only when shipping speed is the constraint.
        # During Drawdown the constraint is factory capacity, which arrives no
        # sooner by mail, so the switch is suppressed and the truck rate kept.
        if (
            stockout_imminent
            and order_shipping_method == SHIPPING_TRUCK
            and mode is not OperatingMode.DRAWDOWN
        ):
            shipping_method = SHIPPING_MAIL
            logger.warning("Day %d: switching to mail to avert stockout", current_day)
        else:
            shipping_method = order_shipping_method

        lead_time = calculate_effective_lead_time(
            order_quantity=order_quantity,
            capacity=capacity,
            shipping_method=shipping_method,
        )
        safety_stock = calculate_safety_stock(
            demand_std=demand_std, lead_time=lead_time
        )
        reorder_point = calculate_reorder_point(
            mode=mode,
            forecast_demand=forecast,
            lead_time=lead_time,
            safety_stock=safety_stock,
            current_day=current_day,
            future_deficit=future_deficit,
        )

        logger.info(
            "Day %d: reorder point=%d, order quantity=%d, shipping=%s, safety stock=%d",
            current_day, reorder_point, order_quantity, shipping_method, safety_stock,
        )

        self.controller.set_reorder_point(reorder_point)
        self.controller.set_order_quantity(order_quantity)
        self.controller.set_shipping_method(shipping_method)
        self.controller.apply_settings()

        decisions = {
            "day": current_day,
            "mode": mode.value,
            "warehouse_inventory": warehouse_inventory,
            "in_transit_inventory": in_transit_inventory,
            "current_inventory": current_inventory,
            "capacity": capacity,
            "demand_mean": round(statistics["mean"], 2),
            "demand_std": round(statistics["std"], 2),
            "future_deficit": future_deficit,
            "stockout_imminent": stockout_imminent,
            "shipping_method": shipping_method,
            "order_quantity": order_quantity,
            "lead_time": lead_time,
            "safety_stock": safety_stock,
            "reorder_point": reorder_point,
        }
        self.decision_log.append(decisions)
        return decisions

    # ------------------------------------------------------------------
    # Decision log
    # ------------------------------------------------------------------

    def save_decision_log(self, path="decision_log.csv") -> Path:
        """Write one row per decision cycle to a CSV file.

        This is the audit trail for a run. Every reorder point the bot set, and
        the state that produced it, can be read back and compared against what
        the game subsequently did.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=DECISION_LOG_COLUMNS)
            writer.writeheader()
            writer.writerows(self.decision_log)

        logger.info("Wrote %d decision rows to %s", len(self.decision_log), path)
        return path

    # ------------------------------------------------------------------
    # Crash recovery
    # ------------------------------------------------------------------

    def _recover(self) -> bool:
        """Discard the dead browser session and build a fresh one."""
        logger.info("Attempting recovery by recreating the browser session")

        attempt = self._consecutive_errors

        try:
            self.controller.close()
        except Exception:
            # The session being replaced is already assumed to be broken.
            pass

        try:
            self.controller = self._controller_factory()
            self.controller.login()
            current_day = self.controller.get_current_day()
            self._consecutive_errors = 0
            notify_recovery(current_day, attempt=attempt)
            logger.info("Recovery succeeded, resumed at day %d", current_day)
            return True
        except Exception as error:
            logger.error("Recovery failed: %s", error, exc_info=True)
            notify_error(f"Recovery failed: {error}", day=self.last_processed_day)
            return False

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self, poll_interval: int = DEFAULT_POLL_INTERVAL_SECONDS):
        """Poll the game and run one decision cycle per new game day.

        Runs until the game passes day 1460 or the process is interrupted. Any
        exception inside the loop is caught, reported, and retried with an
        increasing wait. After three consecutive failures the browser session is
        assumed dead and is rebuilt.
        """
        logger.info("Starting the Single-Region Run bot")

        try:
            self.controller.login()
            logger.info("Logged in")
            notify_startup(self.controller.get_current_day())

            while True:
                try:
                    self.controller.refresh()
                    current_day = self.controller.get_current_day()

                    if current_day > GAME_END_DAY:
                        logger.info("Game ended at day %d", current_day)
                        notify_shutdown("Game ended", day=current_day)
                        break

                    if current_day != self.last_processed_day:
                        logger.info("New day detected: %d", current_day)
                        notify_decision(self.run_cycle(current_day))
                        self.last_processed_day = current_day

                    self._consecutive_errors = 0
                    time.sleep(poll_interval)

                except Exception as error:
                    self._consecutive_errors += 1
                    logger.error(
                        "Error in main loop (%d/%d): %s",
                        self._consecutive_errors, MAX_CONSECUTIVE_ERRORS, error,
                        exc_info=True,
                    )
                    notify_error(
                        f"({self._consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}) {error}",
                        day=self.last_processed_day,
                    )

                    if (
                        self._consecutive_errors >= MAX_CONSECUTIVE_ERRORS
                        and self._controller_factory
                    ):
                        if self._recover():
                            continue
                        time.sleep(poll_interval * 4)
                    else:
                        time.sleep(poll_interval * 2)

        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
            notify_shutdown("Stopped by user", day=self.last_processed_day)

        finally:
            if self.decision_log:
                self.save_decision_log()
            logger.info("Bot shutdown complete")
