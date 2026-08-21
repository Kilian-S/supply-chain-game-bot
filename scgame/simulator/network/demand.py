# Loads demand data from Excel file for multi-region simulation.
# Expects full 4-year demand data (days 1-1460) for proper Holt-Winters forecasting.

import pandas as pd
from pathlib import Path
from typing import Dict, Optional

# Game constants
GAME_START_DAY = 730
GAME_END_DAY = 1460
FIRST_DAY = 1  # Full historical data starts at day 1

# Region names (must match Excel column headers)
REGIONS = ["Calopeia", "Sorange", "Tyran", "Entworpe", "Fardo"]

# Recorded demand for every region across all four years, downloaded from the
# game. Days 1 to 729 are the history the scenario opens with, and days 730
# onwards are what actually happened during the run.
DEMAND_FILE_NAME = "network_all_regions_4yr.xlsx"
DATA_DIRECTORY = Path(__file__).resolve().parents[3] / "data"


class DemandLoader:
    """
    Loads and provides demand data for all regions.

    Reads from a single Excel file with columns:
    - day: Day number (1-1460)
    - Calopeia, Sorange, Tyran, Entworpe, Fardo: Demand per region

    This format matches how the real GameController parses demand data.

    The simulator uses demand from days 730-1460.
    The bot receives historical demand up to the current simulation day.
    """

    def __init__(self, demand_folder: str = None):
        """
        Initialise the demand loader.

        Args:
            demand_folder: Folder holding the demand workbook. Defaults to the
                repository's data directory.
        """
        if demand_folder is None:
            demand_folder = DATA_DIRECTORY

        self.demand_folder = Path(demand_folder)
        self.demand_file = self.demand_folder / DEMAND_FILE_NAME

        # Storage: DataFrame with all demand data
        self._demand_df: Optional[pd.DataFrame] = None
        self._loaded = False

    def load_all(self) -> pd.DataFrame:
        """
        Load demand data from the combined Excel file.

        Returns:
            DataFrame with columns: day, Calopeia, Sorange, Tyran, Entworpe, Fardo
        """
        if self._loaded:
            return self._demand_df

        if not self.demand_file.exists():
            raise FileNotFoundError(f"Demand file not found: {self.demand_file}")

        df = pd.read_excel(self.demand_file)

        # Validate columns
        required_cols = ['day'] + REGIONS
        for col in required_cols:
            if col not in df.columns:
                raise ValueError(f"Missing required column '{col}' in {self.demand_file}")

        # Ensure day column is integer BEFORE sorting
        df['day'] = pd.to_numeric(df['day'], errors='coerce').fillna(0).astype(int)

        # Sort by day and ensure proper indexing
        df = df.sort_values('day').reset_index(drop=True)

        # Ensure demand columns are non-negative integers
        for region in REGIONS:
            df[region] = df[region].fillna(0).astype(int).clip(lower=0)

        self._demand_df = df
        self._loaded = True

        return self._demand_df

    def get_demand_for_day(self, region: str, day: int) -> int:
        """
        Get demand for a specific region and day.

        Args:
            region: Region name (e.g., 'Calopeia')
            day: Day number (1-1460)

        Returns:
            Demand quantity for that day.
        """
        if not self._loaded:
            self.load_all()

        if region not in REGIONS:
            raise ValueError(f"Unknown region: {region}")

        if day < FIRST_DAY or day > GAME_END_DAY:
            return 0

        # Find row for this day
        row = self._demand_df[self._demand_df['day'] == day]
        if row.empty:
            return 0

        return int(row[region].iloc[0])

    def get_all_demand_for_day(self, day: int) -> Dict[str, int]:
        """
        Get demand for all regions on a specific day.

        Args:
            day: Day number (1-1460)

        Returns:
            Dict mapping region name to demand quantity.
        """
        if not self._loaded:
            self.load_all()

        return {
            region: self.get_demand_for_day(region, day)
            for region in REGIONS
        }

    def get_historical_demand_df(self, up_to_day: int) -> pd.DataFrame:
        """
        Get historical demand as DataFrame from day 1 up to specified day.

        This provides the bot with all historical demand for forecasting,
        just like the real game would provide scraped historical data.

        Format matches GameController output:
        - Column 'Day' (capital D) for day numbers
        - Columns for each region

        Args:
            up_to_day: Last day to include (inclusive)

        Returns:
            DataFrame with columns: Day, Calopeia, Sorange, Tyran, Entworpe, Fardo
        """
        if not self._loaded:
            self.load_all()

        # Filter to days up to current day
        mask = self._demand_df['day'] <= up_to_day
        result = self._demand_df[mask][['day'] + REGIONS].copy()

        # Rename 'day' to 'Day' to match GameController format
        result = result.rename(columns={'day': 'Day'})

        return result

    def get_total_demand(self) -> Dict[str, int]:
        """Get total demand across game period (days 730-1460) for each region."""
        if not self._loaded:
            self.load_all()

        mask = (self._demand_df['day'] >= GAME_START_DAY) & (self._demand_df['day'] <= GAME_END_DAY)
        game_period = self._demand_df[mask]

        return {
            region: int(game_period[region].sum())
            for region in REGIONS
        }

    def get_average_demand(self) -> Dict[str, float]:
        """Get average daily demand during game period for each region."""
        if not self._loaded:
            self.load_all()

        mask = (self._demand_df['day'] >= GAME_START_DAY) & (self._demand_df['day'] <= GAME_END_DAY)
        game_period = self._demand_df[mask]

        return {
            region: float(game_period[region].mean())
            for region in REGIONS
        }
