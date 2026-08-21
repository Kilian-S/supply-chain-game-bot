"""Shared Selenium plumbing for driving the Supply Chain Game web interface.

The game is a server-rendered application that opens each facility in its own
popup window, and renders every data table only after a second "data" button is
pressed inside that popup. `BrowserGameController` owns that navigation
mechanic. The Single-Region Run and the Network Run subclass it and add the
facility-specific scraping and form submission that differs between them.

Credentials are read from the environment and are never stored in this file.
Set TEAM_ID, TEAM_PASSWORD, and optionally GAME_URL before running.
"""

import os
import re
import time
import logging

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoAlertPresentException, TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

logger = logging.getLogger(__name__)


DEFAULT_BASE_URL = "https://op.responsive.net/sc/escribano/entry.html"

# Time the game needs to settle after a form submission or a login redirect.
# The pages carry no readiness signal, so these are unavoidable fixed waits.
LOGIN_SETTLE_SECONDS = 2
SUBMIT_SETTLE_SECONDS = 1
DATA_BUTTON_SETTLE_SECONDS = 0.5

# Capacity strings appear in two forms depending on whether the factory is
# operational or still being built, and the number may or may not carry a
# decimal part.
_OPERATIONAL_CAPACITY = re.compile(r"current capacity of (\d+(?:\.\d+)?)")
_SCHEDULED_CAPACITY = re.compile(r"scheduled capacity is (\d+(?:\.\d+)?)")


class GameUnavailableError(RuntimeError):
    """Raised when the game page does not present the element being scraped."""


class BrowserGameController:
    """Selenium driver for the Supply Chain Game, shared by both runs."""

    def __init__(self, base_url: str = None, headless: bool = False, wait_seconds: int = 15):
        options = Options()
        if headless:
            options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        self.driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options,
        )
        self.wait = WebDriverWait(self.driver, wait_seconds)
        self.base_url = base_url or os.environ.get("GAME_URL", DEFAULT_BASE_URL)
        self.main_window = None

        logger.info("Browser controller initialised")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def login(self, team_id: str = None, password: str = None):
        """Navigate to the game, authenticate, and dismiss the post-game alert.

        Credentials default to the TEAM_ID and TEAM_PASSWORD environment
        variables. Both must be set, and neither is ever written to the logs.
        """
        team_id = team_id if team_id is not None else os.environ.get("TEAM_ID", "")
        password = password if password is not None else os.environ.get("TEAM_PASSWORD", "")

        if not team_id or not password:
            raise GameUnavailableError(
                "TEAM_ID and TEAM_PASSWORD must be set in the environment before logging in."
            )

        self.driver.get(self.base_url)

        id_field = self.wait.until(EC.element_to_be_clickable((By.NAME, "id")))
        id_field.clear()
        id_field.send_keys(team_id)

        pw_field = self.wait.until(EC.element_to_be_clickable((By.NAME, "password")))
        pw_field.clear()
        pw_field.send_keys(password)

        self.driver.find_element(By.CSS_SELECTOR, "input[type='submit']").click()
        time.sleep(LOGIN_SETTLE_SECONDS)

        # A JavaScript alert appears only once the game has finished. During a
        # live game there is no alert and this is a no-op.
        try:
            self.driver.switch_to.alert.accept()
            logger.info("Dismissed post-game alert")
        except NoAlertPresentException:
            pass

        self.main_window = self.driver.current_window_handle
        logger.info("Login successful")

    def refresh(self):
        """Re-authenticate to pull a fresh copy of the map screen.

        The game map is server-rendered and never updates in place, so the only
        way to observe a day change is to request the page again. Callers should
        invoke this exactly once per decision cycle, before reading any state.
        """
        self.login()

    def close(self):
        """Shut down the browser."""
        self.driver.quit()

    # ------------------------------------------------------------------
    # Window and popup navigation
    # ------------------------------------------------------------------

    def _ensure_main_window(self):
        """Close every window except the main map, then focus it."""
        for handle in list(self.driver.window_handles):
            if handle != self.main_window:
                self.driver.switch_to.window(handle)
                self.driver.close()
        self.driver.switch_to.window(self.main_window)

    def _await_new_window(self, known_handles: set) -> str:
        """Block until a window outside `known_handles` exists, then focus it.

        Selecting the new handle by set difference keeps the choice
        deterministic when more than one popup is open.
        """
        WebDriverWait(self.driver, 10).until(
            lambda d: set(d.window_handles) - known_handles
        )
        new_handle = (set(self.driver.window_handles) - known_handles).pop()
        self.driver.switch_to.window(new_handle)
        return new_handle

    def _open_popup(self, icon_src: str) -> str:
        """Click a map icon by image source and focus the popup it opens."""
        self._ensure_main_window()
        known = set(self.driver.window_handles)

        button = self.wait.until(
            EC.element_to_be_clickable((By.XPATH, f"//img[@src='{icon_src}']"))
        )
        button.click()

        return self._await_new_window(known)

    def _open_facility_popup(self, servlet: str, region_id: int) -> str:
        """Open a facility popup by region, following the map anchor's href.

        Anchors carry hrefs of the form
        `javascript:openwin('SCFactory?action=change&region=N')`. Matching on
        the href rather than the icon image works for facilities that are still
        under construction, whose icon differs from the operational one. The
        anchor itself has zero size because the image inside it is absolutely
        positioned, so the image is the element that must be clicked.
        """
        self._ensure_main_window()
        known = set(self.driver.window_handles)

        xpath = (
            f"//a[contains(@href, '{servlet}') and "
            f"contains(@href, 'region={region_id}')]/img"
        )
        image = self.wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
        image.click()

        return self._await_new_window(known)

    def _open_factory_popup(self, region_id: int) -> str:
        """Open the factory popup for a region."""
        return self._open_facility_popup("SCFactory", region_id)

    def _open_warehouse_popup(self, region_id: int) -> str:
        """Open the warehouse popup for a region."""
        return self._open_facility_popup("SCWarehouse", region_id)

    def _click_plot_and_show_data(self, plot_value: str) -> str:
        """Open a plot from the current popup and reveal its data table.

        Pressing a plot button opens a further tab showing a chart. The
        underlying table is rendered only after the "data" button on that tab is
        pressed. Returns the handle of the tab holding the table.
        """
        known = set(self.driver.window_handles)

        button = self.wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, f"//input[@type='submit' and @value='{plot_value}']")
            )
        )
        button.click()

        data_tab = self._await_new_window(known)

        # The data button is not consistently labelled across plot pages, so
        # each known variant is tried in turn.
        for selector in (
            (By.NAME, "data"),
            (By.XPATH, "//input[@value='Data' or @value='data']"),
            (By.XPATH, "//button[contains(text(),'Data') or contains(text(),'data')]"),
        ):
            try:
                data_button = WebDriverWait(self.driver, 3).until(
                    EC.element_to_be_clickable(selector)
                )
                data_button.click()
                time.sleep(DATA_BUTTON_SETTLE_SECONDS)
                break
            except TimeoutException:
                continue
        else:
            logger.warning(
                "No data button found for '%s', scraping the DOM as rendered", plot_value
            )

        return data_tab

    def _close_tab_and_popup(self, data_tab: str, popup: str):
        """Close the data tab and its parent popup, then return to the map."""
        self.driver.switch_to.window(data_tab)
        self.driver.close()
        self.driver.switch_to.window(popup)
        self.driver.close()
        self.driver.switch_to.window(self.main_window)

    # ------------------------------------------------------------------
    # Table scraping
    # ------------------------------------------------------------------

    def _scrape_data_table(self):
        """Scrape the data table rendered in `#dataTableDiv1` on this page.

        Returns a `(rows, columns)` pair, where `rows` is a list of dictionaries
        keyed by column name. The game leaves a cell blank when the quantity it
        tracks did not change on that row's timestamp, and blanks are preserved
        as None so that callers can distinguish "unchanged" from "zero".
        """
        header_cells = self.driver.find_elements(
            By.CSS_SELECTOR, "#dataTableDiv1 .table-header td"
        )
        columns = [cell.get_attribute("textContent").strip() for cell in header_cells]

        row_elements = self.driver.find_elements(
            By.CSS_SELECTOR, "#dataTableDiv1 .table-body tr"
        )

        rows = []
        for row_element in row_elements:
            cells = row_element.find_elements(By.TAG_NAME, "td")
            row = {}
            for index, cell in enumerate(cells):
                if index >= len(columns):
                    break
                text = cell.get_attribute("textContent").strip()
                if text == "":
                    row[columns[index]] = None
                    continue
                cleaned = text.replace(",", "")
                try:
                    row[columns[index]] = float(cleaned) if "." in cleaned else int(cleaned)
                except ValueError:
                    row[columns[index]] = text
            if row:
                rows.append(row)

        return rows, columns

    # ------------------------------------------------------------------
    # Readings available on the map screen
    # ------------------------------------------------------------------

    def get_current_day(self) -> int:
        """Read the current game day from the map screen.

        This is a pure read of whatever page is currently loaded. Call
        `refresh()` first if a fresh value is required.
        """
        self._ensure_main_window()

        element = self.wait.until(
            EC.presence_of_element_located(
                (By.XPATH, '//td[@align="right" and contains(., "Day:")]/b[last()]')
            )
        )
        day = int(element.text.strip().replace(",", ""))
        logger.info("Current day: %d", day)
        return day

    def get_cash(self) -> float:
        """Read the current cash balance from the map screen."""
        self._ensure_main_window()

        element = self.wait.until(
            EC.presence_of_element_located((By.XPATH, '//b[starts-with(., "$")]'))
        )
        cash = float(element.text.strip().replace("$", "").replace(",", ""))
        logger.info("Current cash: $%s", f"{cash:,.2f}")
        return cash

    # ------------------------------------------------------------------
    # Shared parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def parse_capacity(page_text: str) -> int:
        """Extract daily production capacity in drums from a factory page.

        An operational factory reports "current capacity of X". A factory whose
        expansion has not yet come online reports only "scheduled capacity is X",
        and contributes nothing to production until it does, so it reads as zero.
        Capacity is reported to two decimal places and is truncated rather than
        rounded, because a factory cannot complete a partial drum in a day.
        """
        operational = _OPERATIONAL_CAPACITY.search(page_text)
        if operational:
            return int(float(operational.group(1)))

        scheduled = _SCHEDULED_CAPACITY.search(page_text)
        if scheduled:
            logger.info(
                "Factory under construction, scheduled capacity %s, reading as 0",
                scheduled.group(1),
            )
            return 0

        raise GameUnavailableError(
            "Factory page contained neither a current nor a scheduled capacity figure."
        )
