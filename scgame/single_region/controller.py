"""Live game controller for the Single-Region Run.

Exposes the same interface as `scgame.simulator.single_region.controller`, so
`SingleRegionBot` drives the real game and the offline simulator through
identical calls.

Only Calopeia exists in this scenario, so every facility lookup is fixed to
region 1.
"""

import logging
import time

import pandas as pd
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support import expected_conditions as EC

from ..common.browser_controller import (
    BrowserGameController,
    GameUnavailableError,
    SUBMIT_SETTLE_SECONDS,
)

logger = logging.getLogger(__name__)

CALOPEIA_REGION_ID = 1
ICON_HEADQUARTERS = "corporate.gif"

# The game's shipping dropdown uses lowercase option values.
SHIPPING_OPTION_VALUES = {"TRUCK": "truck", "MAIL": "mail"}


class LiveSingleRegionController(BrowserGameController):
    """Reads and writes the live game for the Single-Region Run."""

    def __init__(self, base_url: str = None, headless: bool = False):
        super().__init__(base_url=base_url, headless=headless)

        # One warehouse scrape yields both the on-hand and the in-transit
        # figures, so the result is cached and both getters read from it.
        # `refresh()` clears the cache at the start of each cycle.
        self._cached_warehouse = None
        self._cached_in_transit = None

        self._pending_reorder_point = None
        self._pending_order_quantity = None
        self._pending_shipping_method = None

    def refresh(self):
        """Reload the map and discard the previous cycle's cached readings."""
        self._cached_warehouse = None
        self._cached_in_transit = None
        super().refresh()

    # ------------------------------------------------------------------
    # Reading state
    # ------------------------------------------------------------------

    def _scrape_inventory(self):
        """Read warehouse and in-transit quantities from the inventory plot.

        The inventory table records state changes rather than daily levels, and
        leaves a column blank on any row where that column did not change. The
        current level of each column is therefore the last non-blank value in
        it, which is found by walking the table backwards.
        """
        popup = self._open_warehouse_popup(CALOPEIA_REGION_ID)
        data_tab = self._click_plot_and_show_data("plot inventory")
        rows, columns = self._scrape_data_table()
        self._close_tab_and_popup(data_tab, popup)

        logger.info("Inventory table: columns=%s, rows=%d", columns, len(rows))

        by_lowercase_name = {column.lower(): column for column in columns}
        warehouse_key = by_lowercase_name.get("warehouse")
        mail_key = by_lowercase_name.get("mail")
        truck_key = by_lowercase_name.get("truck")

        warehouse = mail = truck = None
        for row in reversed(rows):
            if warehouse is None and warehouse_key and row.get(warehouse_key) is not None:
                warehouse = int(row[warehouse_key])
            if mail is None and mail_key and row.get(mail_key) is not None:
                mail = int(row[mail_key])
            if truck is None and truck_key and row.get(truck_key) is not None:
                truck = int(row[truck_key])
            if warehouse is not None and mail is not None and truck is not None:
                break

        self._cached_warehouse = warehouse or 0
        self._cached_in_transit = (mail or 0) + (truck or 0)

        logger.info(
            "Inventory: warehouse=%d, in transit=%d (mail=%d, truck=%d)",
            self._cached_warehouse, self._cached_in_transit, mail or 0, truck or 0,
        )

    def get_warehouse_inventory(self) -> int:
        """Return drums on the warehouse shelf and available to sell."""
        if self._cached_warehouse is None:
            self._scrape_inventory()
        return self._cached_warehouse

    def get_in_transit_inventory(self) -> int:
        """Return drums dispatched from the factory but not yet arrived."""
        if self._cached_in_transit is None:
            self._scrape_inventory()
        return self._cached_in_transit

    def get_capacity(self) -> int:
        """Return the factory's daily production capacity in drums."""
        self._open_factory_popup(CALOPEIA_REGION_ID)
        page_text = self.driver.find_element(By.TAG_NAME, "body").text
        capacity = self.parse_capacity(page_text)

        self.driver.close()
        self.driver.switch_to.window(self.main_window)

        logger.info("Capacity: %d", capacity)
        return capacity

    def get_historical_demand(self) -> pd.DataFrame:
        """Return every day of demand observed so far.

        Returns a frame with columns `day` and `demand`, ordered from day 1 to
        the current day.
        """
        popup = self._open_popup(ICON_HEADQUARTERS)
        data_tab = self._click_plot_and_show_data("plot demand")
        rows, columns = self._scrape_data_table()
        self._close_tab_and_popup(data_tab, popup)

        if len(columns) < 2:
            raise GameUnavailableError(
                f"Demand table had too few columns to read: {columns}"
            )

        day_column = columns[0]
        demand_column = next(
            (column for column in columns[1:] if "calopeia" in column.lower()),
            columns[1],
        )

        days, demands = [], []
        for row in rows:
            day, demand = row.get(day_column), row.get(demand_column)
            if day is not None and demand is not None:
                days.append(int(day))
                demands.append(float(demand))

        frame = pd.DataFrame({"day": days, "demand": demands})
        logger.info("Demand history scraped: %d rows", len(frame))
        return frame

    # ------------------------------------------------------------------
    # Writing settings
    # ------------------------------------------------------------------
    # The three settings share one form and one submit button, so they are
    # buffered here and written together by apply_settings(). This keeps the
    # game to a single state change per cycle, which matters because the
    # assignment warns that concurrent changes can be applied out of order.

    def set_reorder_point(self, reorder_point: int):
        """Buffer the reorder point for the next apply_settings call."""
        self._pending_reorder_point = int(reorder_point)

    def set_order_quantity(self, quantity: int):
        """Buffer the order quantity for the next apply_settings call."""
        self._pending_order_quantity = int(quantity)

    def set_shipping_method(self, method: str):
        """Buffer the shipping method, either TRUCK or MAIL."""
        if method not in SHIPPING_OPTION_VALUES:
            raise ValueError(f"Unknown shipping method: {method}")
        self._pending_shipping_method = method

    def apply_settings(self):
        """Write every buffered setting to the factory form and submit it."""
        if (
            self._pending_reorder_point is None
            and self._pending_order_quantity is None
            and self._pending_shipping_method is None
        ):
            logger.info("apply_settings called with nothing buffered")
            return

        popup = self._open_factory_popup(CALOPEIA_REGION_ID)

        if self._pending_reorder_point is not None:
            field = self.wait.until(
                EC.element_to_be_clickable((By.XPATH, "//input[@name='point1']"))
            )
            field.clear()
            field.send_keys(str(self._pending_reorder_point))

        if self._pending_order_quantity is not None:
            field = self.wait.until(
                EC.element_to_be_clickable((By.XPATH, "//input[@name='quant1']"))
            )
            field.clear()
            field.send_keys(str(self._pending_order_quantity))

        if self._pending_shipping_method is not None:
            Select(self.driver.find_element(By.ID, "ship")).select_by_value(
                SHIPPING_OPTION_VALUES[self._pending_shipping_method]
            )

        # The submit control is an input of type button carrying an onclick
        # handler rather than a form submit, so it has to be clicked directly.
        self.wait.until(
            EC.element_to_be_clickable((By.XPATH, "//input[@name='btnSubmit']"))
        ).click()
        time.sleep(SUBMIT_SETTLE_SECONDS)

        logger.info(
            "Applied settings: reorder point=%s, quantity=%s, shipping=%s",
            self._pending_reorder_point,
            self._pending_order_quantity,
            self._pending_shipping_method,
        )

        self._pending_reorder_point = None
        self._pending_order_quantity = None
        self._pending_shipping_method = None

        # Submitting sometimes closes the popup from JavaScript, so its presence
        # is checked before trying to close it again.
        if popup in self.driver.window_handles:
            self.driver.switch_to.window(popup)
            self.driver.close()
        self.driver.switch_to.window(self.main_window)
