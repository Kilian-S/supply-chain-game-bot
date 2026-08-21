"""Command line entry point for the Network Run simulator.

Replays the assessed five-region strategy across days 730 to 1460.

    python -m scgame.simulator.network.run
    python -m scgame.simulator.network.run --headless
    python -m scgame.simulator.network.run --fardo-capacity 17

The build schedule below is the one that was played. Factories at Sorange and
Fardo, and warehouses at Sorange, Tyran, and Fardo, are all ordered on day 730.
Factories take 90 days to come online and warehouses take 60, so nothing new is
productive before day 790. Calopeia already has a factory and a warehouse, and
its factory is expanded from 70 to 75 drums per day on the same day-730 order.
"""

import argparse
import logging
import sys
import warnings

from ...network.bot import NetworkBot
from .analysis import NetworkAnalyser
from .build_schedule import BuildSchedule
from .controller import SimulatedNetworkController
from .dashboard import NetworkVisualizer
from .engine import NetworkEngine

logger = logging.getLogger(__name__)

# Cash held on day 730, carried over from the opening two years of the scenario.
STARTING_CASH = 6_796_510.0

# Inventory standing in the Calopeia warehouse on day 730.
CALOPEIA_OPENING_INVENTORY = 2528

DEFAULT_DASHBOARD_SPEED = 50


def played_schedule(fardo_capacity: int = 20) -> BuildSchedule:
    """Return the build and capacity plan that was actually played.

    Fardo capacity is exposed because the coursework concluded afterwards that
    20 drums per day was more than the island needed, and that around 17 would
    have left less stock stranded there at the end.
    """
    return BuildSchedule(
        name="as played",
        factory_orders={
            "Calopeia_Factory": None,   # Already standing on day 730
            "Sorange_Factory": 730,
            "Fardo_Factory": 730,
        },
        factory_capacities={
            "Calopeia_Factory": 75,
            "Sorange_Factory": 85,
            "Fardo_Factory": fardo_capacity,
        },
        factory_starting_capacities={
            "Calopeia_Factory": 70,
            "Sorange_Factory": 0,
            "Fardo_Factory": 0,
        },
        capacity_expansion_orders={
            "Calopeia_Factory": 730,
        },
        warehouse_orders={
            "Calopeia_WH": None,        # Already standing on day 730
            "Sorange_WH": 730,
            "Tyran_WH": 730,
            "Fardo_WH": 730,
        },
        initial_inventory={
            "Calopeia_WH": CALOPEIA_OPENING_INVENTORY,
        },
    )


def silence_forecast_warnings():
    """Suppress numerical noise from the forecasting routines."""
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    warnings.filterwarnings("ignore", category=UserWarning, module="statsmodels")


def build_run(schedule: BuildSchedule, starting_cash: float):
    """Assemble the engine, the controller, and the bot for one run."""
    engine = NetworkEngine(
        build_schedule=schedule,
        starting_cash=starting_cash,
    )
    controller = SimulatedNetworkController(engine=engine)
    controller.login()
    return engine, controller, NetworkBot(controller)


def run_headless(engine, bot) -> int:
    """Run the whole horizon with no dashboard, reporting progress only.

    A failed decision cycle is allowed to propagate. Continuing past one would
    leave the engine running on the previous day's settings and produce a result
    that looks plausible but means nothing.
    """
    days = 0
    while not engine.is_game_over:
        bot.run_cycle(engine.current_day)
        engine.step()
        days += 1
        if days % 100 == 0:
            print(f"  day {engine.current_day}, cash ${engine.cash:,.0f}")
    return days


def run_with_dashboard(engine, bot, speed: int):
    """Run the horizon behind the live terminal dashboard.

    Bot logging is redirected into the dashboard's own panel for the duration,
    because anything written to the console would corrupt the rendered layout.
    Failed cycles are collected and reported once the dashboard closes, since
    raising inside the render loop would leave the terminal in a broken state.
    """
    visualiser = NetworkVisualizer(engine, speed=speed)

    bot_logger = logging.getLogger("scgame.network.bot")
    bot_logger.setLevel(logging.INFO)
    root_logger = logging.getLogger()

    bot_handlers = list(bot_logger.handlers)
    root_handlers = list(root_logger.handlers)
    for handler in bot_handlers:
        bot_logger.removeHandler(handler)
    for handler in root_handlers:
        root_logger.removeHandler(handler)

    panel_handler = visualiser.get_log_handler()
    bot_logger.addHandler(panel_handler)

    failures = []

    def cycle():
        try:
            bot.run_cycle(engine.current_day)
        except Exception as error:
            failures.append((engine.current_day, error))
            bot_logger.error("Decision cycle failed: %s", error)

    try:
        visualiser.run(bot_cycle_callback=cycle)
    finally:
        bot_logger.removeHandler(panel_handler)
        for handler in bot_handlers:
            bot_logger.addHandler(handler)
        for handler in root_handlers:
            root_logger.addHandler(handler)

    if failures:
        print(f"\nWarning: {len(failures)} decision cycles failed. "
              f"First failure was on day {failures[0][0]}: {failures[0][1]}")

    return failures


def build_parser() -> argparse.ArgumentParser:
    """Define the command line interface."""
    parser = argparse.ArgumentParser(
        description="Simulate the Network Run of the Supply Chain Game."
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Run without the live dashboard, which is faster.",
    )
    parser.add_argument(
        "--speed", type=int, default=DEFAULT_DASHBOARD_SPEED,
        help=f"Dashboard speed in days per second (default {DEFAULT_DASHBOARD_SPEED}).",
    )
    parser.add_argument(
        "--fardo-capacity", type=int, default=20,
        help="Fardo factory capacity in drums per day (default 20, as played).",
    )
    parser.add_argument(
        "--cash", type=float, default=STARTING_CASH,
        help="Cash on day 730 (default 6,796,510, as played).",
    )
    parser.add_argument(
        "--no-charts", action="store_true",
        help="Print the summary but skip the interactive analysis screens.",
    )
    return parser


def main(argv=None) -> int:
    """Run the simulator from the command line."""
    args = build_parser().parse_args(argv)
    silence_forecast_warnings()

    schedule = played_schedule(fardo_capacity=args.fardo_capacity)

    print("Network Run simulator")
    print(schedule.summary())
    print()

    engine, _controller, bot = build_run(schedule, args.cash)

    if args.headless:
        logging.basicConfig(
            level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s"
        )
        days = run_headless(engine, bot)
        print(f"\nSimulated {days} days.")
    else:
        print("Controls: [space] pause, [s] step, [+/-] speed, [q] quit")
        print()
        run_with_dashboard(engine, bot, args.speed)

    if not engine.daily_records:
        print("The simulation produced no records.")
        return 1

    analyser = NetworkAnalyser(engine)
    analyser.print_summary()

    if not args.no_charts:
        print("\nOpening the analysis screens.")
        print("Navigation: [1-9] jump to a screen, [left/right] cycle, [q] quit")
        analyser.show()

    return 0


if __name__ == "__main__":
    sys.exit(main())
