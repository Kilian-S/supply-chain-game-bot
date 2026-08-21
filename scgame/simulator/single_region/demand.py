"""Demand series for the Single-Region Run simulator.

Two sources are available. The recorded series is the actual Calopeia demand for
all 1,460 days, downloaded from the game after the run closed, and is the
default because it makes the simulation a faithful replay of the scenario that
was played. The synthetic series is generated from the statistical
characteristics of the first two years and exists so that the strategy can be
tested against demand it has never seen.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ...common.economics import GAME_END_DAY, ENDGAME_START_DAY, SEASONAL_PERIOD

DATA_DIRECTORY = Path(__file__).resolve().parents[3] / "data"
RECORDED_DEMAND_PATH = DATA_DIRECTORY / "calopeia_demand_4yr.xlsx"
HISTORICAL_DEMAND_PATH = DATA_DIRECTORY / "calopeia_demand_historical.xlsx"


@dataclass
class SyntheticDemandConfig:
    """Parameters for generating a Calopeia-like demand series.

    The defaults are measured from the 730 days of history the game supplies
    before the run begins.
    """

    base_mean: float = 38.71
    standard_deviation: float = 26.61
    seasonal_amplitude: float = 35.0
    peak_day_of_year: int = 200
    noise_factor: float = 0.5
    minimum_demand: int = 1
    maximum_demand: int = 133
    seed: int = 42


def load_recorded_demand(path=None) -> np.ndarray:
    """Load the actual demand series for all 1,460 days.

    Returns an array indexed from day 1, so element 0 is day 1.
    """
    path = Path(path) if path else RECORDED_DEMAND_PATH
    if not path.exists():
        raise FileNotFoundError(f"Recorded demand file not found: {path}")

    frame = pd.read_excel(path)
    frame.columns = [str(column).strip() for column in frame.columns]

    if "demand" in frame.columns:
        column = "demand"
    elif "Calopeia" in frame.columns:
        column = "Calopeia"
    else:
        column = frame.columns[1]

    demand = frame[column].values[:GAME_END_DAY].astype(float)
    if len(demand) < GAME_END_DAY:
        raise ValueError(
            f"Recorded demand covers {len(demand)} days, expected {GAME_END_DAY}."
        )
    return demand


def generate_synthetic_demand(config: SyntheticDemandConfig = None) -> np.ndarray:
    """Build a full 1,460-day series from real history plus a generated future.

    Days 1 to 730 are the real history the game supplies. Days 731 onwards are
    generated as a sinusoidal seasonal component about the historical mean, plus
    Gaussian noise, with the scripted endgame decline applied over the final
    thirty days.
    """
    config = config or SyntheticDemandConfig()
    rng = np.random.default_rng(config.seed)

    history = pd.read_excel(HISTORICAL_DEMAND_PATH)
    history.columns = [str(column).strip() for column in history.columns]
    column = "demand" if "demand" in history.columns else history.columns[1]
    observed = history[column].values[:730].astype(float)

    if len(observed) < 730:
        raise ValueError(
            f"Historical demand covers {len(observed)} days, expected 730."
        )

    generated = np.zeros(GAME_END_DAY - 730)
    decline_period = GAME_END_DAY - ENDGAME_START_DAY

    for index in range(len(generated)):
        day = 731 + index

        day_of_year = (day - 1) % SEASONAL_PERIOD
        seasonal = config.seasonal_amplitude * np.sin(
            2 * np.pi * (day_of_year - config.peak_day_of_year) / SEASONAL_PERIOD
        )
        noise = rng.normal(0, config.standard_deviation * config.noise_factor)
        demand = config.base_mean + seasonal + noise

        if day > ENDGAME_START_DAY:
            demand *= (GAME_END_DAY - day) / decline_period

        generated[index] = np.clip(
            demand, config.minimum_demand, config.maximum_demand
        )

    return np.concatenate([observed, generated])
