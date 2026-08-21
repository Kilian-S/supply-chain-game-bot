"""Live game controller for the Network Run.

Drives the five-region scenario, in which one map carries three factories and
four warehouses, each with its own reorder point, batch size, shipping method,
and priority. Facilities are addressed by region number rather than by icon
image, because a facility under construction shows a different icon from an
operational one and the region number does not change.

Shared browser plumbing lives in `scgame.common.browser_controller`. Only the
multi-region scraping and form submission is here.

Credentials are read from the environment. Set TEAM_ID and TEAM_PASSWORD before
running, and optionally GAME_URL.
"""

import time
import logging

import pandas as pd
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from ..common.browser_controller import (
    BrowserGameController,
    GameUnavailableError,
)
from .config import NETWORK

logger = logging.getLogger(__name__)

ICON_HEADQUARTERS = "corporate.gif"

# The game's shipping dropdown uses lowercase option values.
SHIPPING_OPTION_VALUES = {
    "TRUCK": "truck",
    "MAIL": "mail",
    "truck": "truck",
    "mail": "mail",
}

# Form fields on the factory page are indexed by region number rather than by
# warehouse name, so both spellings of each warehouse map to its region.
WAREHOUSE_REGION_IDS = {
    "Calopeia": 1, "Calopeia_WH": 1,
    "Sorange": 2, "Sorange_WH": 2,
    "Tyran": 3, "Tyran_WH": 3,
    "Entworpe": 4,
    "Fardo": 5, "Fardo_WH": 5,
}


class LiveNetworkController(BrowserGameController):
    """Reads and writes the live game for the Network Run."""

    def get_all_demand(self) -> pd.DataFrame:
        """Scrape the demand history for every region in one pass.

        Headquarters plots all five regions on a single chart, so one visit
        returns the whole picture. Returns a frame with a `Day` column and one
        column per region, ordered from day 1 to the current day. Regions whose
        demand has not started yet report zero rather than being absent.
        """
        popup = self._open_popup(ICON_HEADQUARTERS)
        data_tab = self._click_plot_and_show_data("plot demand")
        rows, columns = self._scrape_data_table()
        self._close_tab_and_popup(data_tab, popup)

        # Build DataFrame from scraped rows
        # columns should be like: ['Day', 'Calopeia', 'Sorange', 'Tyran', 'Entworpe', 'Fardo']
        df = pd.DataFrame(rows)

        # Ensure 'Day' column exists and is named correctly
        day_col = None
        for col in df.columns:
            if col.lower() == 'day':
                day_col = col
                break
        if day_col and day_col != 'Day':
            df = df.rename(columns={day_col: 'Day'})

        # Convert numeric columns
        for col in df.columns:
            if col != 'Day':
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        logger.info(f"All demand scraped: {len(df)} rows, columns={list(df.columns)}")
        return df

    def get_warehouse_state(self, warehouse: str) -> dict:
        """
        Scrape inventory state for a specific warehouse.

        Args:
            warehouse: Warehouse name (e.g., 'Calopeia_WH', 'Tyran_WH')

        Returns:
            Dict with keys:
                'inventory': int - drums currently in warehouse
                'mail': int - drums in transit via mail
                'truck': int - drums in transit via truck
        """
        # Get region ID from network config
        region_id = NETWORK.warehouses[warehouse].region_id

        # Open warehouse popup using href-based navigation
        popup = self._open_warehouse_popup(region_id)

        # Check if warehouse is under construction (no "plot inventory" button)
        plot_buttons = self.driver.find_elements(
            By.XPATH, "//input[@type='submit' and @value='plot inventory']"
        )

        if not plot_buttons:
            # Under construction - no inventory data available
            logger.info(f"{warehouse} is under construction - returning zeros")
            self.driver.close()
            self.driver.switch_to.window(self.main_window)
            return {'inventory': 0, 'mail': 0, 'truck': 0}

        # Warehouse is operational - scrape inventory data
        # Wrap in try/except for newly online warehouses with no inventory history
        try:
            data_tab = self._click_plot_and_show_data("plot inventory")
            rows, columns = self._scrape_data_table()
            self._close_tab_and_popup(data_tab, popup)
        except TimeoutException:
            # A warehouse that opened today has no inventory history to plot.
            logger.info("%s is online but has no inventory history yet", warehouse)
            self._ensure_main_window()
            return {'inventory': 0, 'mail': 0, 'truck': 0}

        logger.info(f"{warehouse} inventory table: columns={columns}, rows={len(rows)}")

        # Case-insensitive column lookup
        col_map = {c.lower(): c for c in columns}
        wh_key = col_map.get("warehouse")
        mail_key = col_map.get("mail")
        truck_key = col_map.get("truck")

        warehouse_inv = None
        mail_inv = None
        truck_inv = None

        # Walk backwards to find most recent non-empty values
        for row in reversed(rows):
            if warehouse_inv is None and wh_key and row.get(wh_key) is not None:
                warehouse_inv = int(row[wh_key])
            if mail_inv is None and mail_key and row.get(mail_key) is not None:
                mail_inv = int(row[mail_key])
            if truck_inv is None and truck_key and row.get(truck_key) is not None:
                truck_inv = int(row[truck_key])
            if warehouse_inv is not None and mail_inv is not None and truck_inv is not None:
                break

        result = {
            'inventory': warehouse_inv if warehouse_inv is not None else 0,
            'mail': mail_inv if mail_inv is not None else 0,
            'truck': truck_inv if truck_inv is not None else 0,
        }

        logger.info(f"{warehouse} state: {result}")
        return result

    def get_capacity(self, factory: str) -> int:
        """Return a factory's daily production capacity in drums.

        A factory whose construction or expansion has not yet completed reports
        only a scheduled capacity, and produces nothing until it lands, so it
        reads as zero.
        """
        region_id = NETWORK.factories[factory].region_id
        self._open_factory_popup(region_id)

        page_text = self.driver.find_element(By.TAG_NAME, "body").text
        try:
            capacity = self.parse_capacity(page_text)
        except GameUnavailableError:
            logger.warning("%s: no capacity figure on the page, reading as 0", factory)
            capacity = 0

        self.driver.close()
        self.driver.switch_to.window(self.main_window)

        logger.info("%s capacity: %d", factory, capacity)
        return capacity

    def apply_factory_settings(self, factory: str, routes: list):
        """
        Open factory popup, set all route parameters, click ok.

        Form fields are indexed by REGION NUMBER:
        - point1, quant1, ship1, priority1 for Calopeia (region 1)
        - point3, quant3, ship3, priority3 for Tyran (region 3)
        - etc.

        Args:
            factory: Factory name (e.g., 'Calopeia_Factory')
            routes: List of route setting dicts, each containing:
                - 'warehouse': str (e.g., 'Calopeia', 'Tyran')
                - 'shipping_method': str ('truck' or 'mail')
                - 'order_point': int
                - 'quantity': int
                - 'priority': int (1-5)

        Example:
            apply_factory_settings('Calopeia_Factory', [
                {'warehouse': 'Calopeia', 'shipping_method': 'truck',
                 'order_point': 5000, 'quantity': 200, 'priority': 5},
                {'warehouse': 'Tyran', 'shipping_method': 'truck',
                 'order_point': 100, 'quantity': 200, 'priority': 3},
            ])
        """
        if not routes:
            logger.info(f"apply_factory_settings({factory}): no routes to configure")
            return

        # Get region ID from network config
        region_id = NETWORK.factories[factory].region_id

        # Open factory popup using href-based navigation
        popup = self._open_factory_popup(region_id)

        # Wait for popup content to load
        time.sleep(1)

        # For each route, set values using region-numbered field names
        routes_set = []  # Track which routes were successfully set
        routes_skipped = []  # Track which routes were skipped (not built)
        for route in routes:
            warehouse_label = route['warehouse']
            ship_method = route['shipping_method']
            order_point = route['order_point']
            quantity = route['quantity']
            priority = route['priority']

            # Get region number for this warehouse
            region_num = WAREHOUSE_REGION_IDS.get(warehouse_label)
            if region_num is None:
                logger.error(f"Unknown warehouse: {warehouse_label}")
                continue

            # Pre-check: does this route exist in the form?
            # Use find_elements (returns empty list if not found, no exception)
            point_elements = self.driver.find_elements(By.XPATH, f"//input[@name='point{region_num}']")
            if not point_elements:
                logger.info(f"Route {factory} -> {warehouse_label} not available yet (warehouse not built)")
                routes_skipped.append(warehouse_label)
                continue

            logger.info(f"Setting {factory} -> {warehouse_label} (region {region_num}): "
                       f"ship={ship_method}, rop={order_point}, qty={quantity}, pri={priority}")

            try:
                # Order point: input name="point{N}"
                point_input = point_elements[0]
                point_input.clear()
                point_input.send_keys(str(order_point))

                # Order quantity: input name="quant{N}"
                quant_input = self.driver.find_element(By.XPATH, f"//input[@name='quant{region_num}']")
                quant_input.clear()
                quant_input.send_keys(str(quantity))

                # Priority: input name="priority{N}"
                pri_input = self.driver.find_element(By.XPATH, f"//input[@name='priority{region_num}']")
                pri_input.clear()
                pri_input.send_keys(str(priority))

                # Shipping method: select name="ship{N}"
                ship_select = self.driver.find_element(By.XPATH, f"//select[@name='ship{region_num}']")
                Select(ship_select).select_by_value(SHIPPING_OPTION_VALUES[ship_method])

                routes_set.append(warehouse_label)

            except Exception as e:
                logger.error(f"Failed to set route {factory} -> {warehouse_label}: {e}")
                raise

        # Reset out-of-system routes to zero (defense in depth)
        # Find all point inputs in the form and disable any we didn't explicitly set
        requested_regions = {WAREHOUSE_REGION_IDS.get(r['warehouse']) for r in routes}
        routes_disabled = []

        # Check each possible region (1-5)
        for region_num, warehouse_label in [(1, 'Calopeia'), (2, 'Sorange'), (3, 'Tyran'), (5, 'Fardo')]:
            # Skip if this region was in our requested routes
            if region_num in requested_regions:
                continue

            # Check if this route exists in the form
            point_elements = self.driver.find_elements(By.XPATH, f"//input[@name='point{region_num}']")
            if not point_elements:
                continue  # Route doesn't exist in form (warehouse not built)

            # This is an out-of-system route that exists - disable it
            try:
                point_input = point_elements[0]
                point_input.clear()
                point_input.send_keys("0")

                quant_input = self.driver.find_element(By.XPATH, f"//input[@name='quant{region_num}']")
                quant_input.clear()
                quant_input.send_keys("0")

                routes_disabled.append(warehouse_label)
                logger.info(f"Disabled out-of-system route {factory} -> {warehouse_label}")

            except Exception as e:
                logger.warning(f"Failed to disable route {factory} -> {warehouse_label}: {e}")

        # Only submit if we set or disabled some routes
        if len(routes_set) == 0 and len(routes_disabled) == 0:
            logger.info(f"No routes available for {factory}, skipping submit")
            if routes_skipped:
                logger.info(f"  Skipped (not built): {', '.join(routes_skipped)}")
        else:
            # Click submit button
            try:
                submit_btn = self.wait.until(
                    EC.element_to_be_clickable((By.NAME, "btnSubmit"))
                )
                submit_btn.click()
                time.sleep(1)
            except Exception as e:
                logger.error(f"Failed to submit factory settings: {e}")
                raise

            # Build detailed log message
            log_parts = [f"Applied {factory}:"]
            if routes_set:
                log_parts.append(f"set=[{', '.join(routes_set)}]")
            if routes_disabled:
                log_parts.append(f"disabled=[{', '.join(routes_disabled)}]")
            if routes_skipped:
                log_parts.append(f"skipped=[{', '.join(routes_skipped)}]")
            logger.info(" ".join(log_parts))

        # Close factory popup
        if popup in self.driver.window_handles:
            self.driver.switch_to.window(popup)
            self.driver.close()
        self.driver.switch_to.window(self.main_window)
