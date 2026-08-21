"""Command line entry point for the Single-Region Run simulator.

Runs the assessed strategy across days 730 to 1460 and reports what it earned.

    python -m scgame.simulator.single_region.run
    python -m scgame.simulator.single_region.run --synthetic --seed 7
    python -m scgame.simulator.single_region.run --capacity 55 --plot results.png
"""

import argparse
import logging
import sys
import warnings

from ...single_region.bot import SingleRegionBot
from .controller import SimulatedSingleRegionController
from .demand import (
    SyntheticDemandConfig,
    generate_synthetic_demand,
    load_recorded_demand,
)
from .engine import SimulationConfig, SingleRegionEngine

logger = logging.getLogger(__name__)


def silence_forecast_warnings():
    """Suppress the numerical noise that Holt-Winters emits during fitting.

    Fitting a 365-period seasonal model to a short and highly variable series
    produces a stream of overflow and convergence warnings from the optimiser on
    almost every day of the run. They say nothing the operator can act on, and
    they bury the output that matters.
    """
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    warnings.filterwarnings("ignore", category=UserWarning, module="statsmodels")
    try:
        from statsmodels.tools.sm_exceptions import ConvergenceWarning

        warnings.filterwarnings("ignore", category=ConvergenceWarning)
    except ImportError:
        pass


def simulate(config: SimulationConfig, demand, progress_every: int = 100):
    """Run the whole horizon and return the engine and the bot that drove it.

    Exceptions raised inside a decision cycle are logged and re-raised rather
    than swallowed. A silent failure would leave the engine running on stale
    settings and produce a plausible but meaningless result.
    """
    engine = SingleRegionEngine(config, demand)
    controller = SimulatedSingleRegionController(engine)
    bot = SingleRegionBot(controller)
    controller.login()

    days_run = 0
    while not engine.is_game_over:
        current_day = engine.current_day

        decisions = bot.run_cycle(current_day)
        controller.set_diagnostics(
            decisions["mode"],
            decisions["safety_stock"],
            decisions["future_deficit"],
        )

        engine.step()
        days_run += 1

        if progress_every and days_run % progress_every == 0:
            print(f"  day {engine.current_day}, cash ${engine.cash:,.0f}")

    return engine, bot


def print_summary(engine: SingleRegionEngine):
    """Print the financial and operational result of a completed run."""
    summary = engine.financial_summary()
    records = engine.daily_records

    print()
    print("=" * 62)
    print("SINGLE-REGION RUN, SIMULATED RESULT")
    print("=" * 62)
    print(f"Horizon: day {records[0].day} to day {records[-1].day} "
          f"({len(records)} days)")

    print("\nProfit and loss")
    rows = [
        ("Revenue", summary["total_revenue"]),
        ("Production, variable", -summary["total_variable_production_cost"]),
        ("Production, fixed", -summary["total_fixed_production_cost"]),
        ("Shipping", -summary["total_shipping_cost"]),
        ("Customer fulfilment", -summary["total_fulfilment_cost"]),
        ("Holding", -summary["total_holding_cost"]),
        ("Capital expenditure", -summary["total_capex"]),
        ("Interest earned", summary["total_interest"]),
    ]
    for label, value in rows:
        print(f"  {label:<24}{value:>16,.0f}")
    print(f"  {'-' * 40}")
    print(f"  {'Net profit':<24}{summary['total_profit']:>16,.0f}")
    print(f"  {'Final cash':<24}{summary['cash']:>16,.0f}")

    print("\nService")
    print(f"  {'Total demand':<24}{summary['total_demand']:>16,} drums")
    print(f"  {'Drums sold':<24}{summary['total_sales']:>16,} drums")
    print(f"  {'Lost demand':<24}{summary['total_stockouts']:>16,} drums")
    print(f"  {'Item fill rate':<24}{summary['item_fill_rate'] * 100:>15.2f}%")
    print(f"  {'Lost contribution':<24}"
          f"{summary['stockout_opportunity_cost']:>16,.0f}")
    print(f"  {'Leftover inventory':<24}{summary['leftover_inventory']:>16,} drums")

    mode_days = {}
    for record in records:
        mode_days[record.mode or "none"] = mode_days.get(record.mode or "none", 0) + 1

    print("\nDays in each operating mode")
    for mode, days in sorted(mode_days.items(), key=lambda item: -item[1]):
        print(f"  {mode.capitalize():<24}{days:>6} days "
              f"({days / len(records) * 100:>5.1f}%)")
    print("=" * 62)


def build_parser() -> argparse.ArgumentParser:
    """Define the command line interface."""
    parser = argparse.ArgumentParser(
        description="Simulate the Single-Region Run of the Supply Chain Game."
    )
    parser.add_argument(
        "--capacity", type=int, default=None,
        help="Final factory capacity in drums per day (default 50, as played).",
    )
    parser.add_argument(
        "--starting-capacity", type=int, default=None,
        help="Capacity before the expansion lands (default 30, as played).",
    )
    parser.add_argument(
        "--inventory", type=int, default=None,
        help="Warehouse inventory on day 730 (default 500).",
    )
    parser.add_argument(
        "--synthetic", action="store_true",
        help="Generate demand statistically instead of replaying the recorded series.",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Seed for synthetic demand (default 42).",
    )
    parser.add_argument(
        "--demand-file", type=str, default=None,
        help="Path to an alternative recorded demand workbook.",
    )
    parser.add_argument(
        "--decision-log", type=str, default=None,
        help="Write every decision cycle to this CSV path.",
    )
    parser.add_argument(
        "--plot", type=str, default=None,
        help="Write the analysis figure to this path instead of displaying it.",
    )
    parser.add_argument(
        "--no-plot", action="store_true",
        help="Skip the analysis figure entirely.",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Show the bot's per-day reasoning.",
    )
    return parser


def main(argv=None) -> int:
    """Run the simulator from the command line."""
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    silence_forecast_warnings()

    config = SimulationConfig()
    if args.capacity is not None:
        config.expanded_capacity = args.capacity
    if args.starting_capacity is not None:
        config.starting_capacity = args.starting_capacity
    if args.inventory is not None:
        config.initial_warehouse = args.inventory

    if args.synthetic:
        demand = generate_synthetic_demand(SyntheticDemandConfig(seed=args.seed))
        source = f"synthetic, seed {args.seed}"
    else:
        demand = load_recorded_demand(args.demand_file)
        source = "recorded Calopeia demand"

    print("Single-Region Run simulator")
    print(f"  demand source      {source}")
    print(f"  capacity           {config.starting_capacity} rising to "
          f"{config.expanded_capacity} drums/day on day "
          f"{config.start_day + config.capacity_online_delay}")
    print(f"  opening inventory  {config.initial_warehouse} drums")
    print(f"  opening cash       ${config.starting_cash:,.0f}")
    print()

    engine, bot = simulate(config, demand)
    print_summary(engine)

    if args.decision_log:
        path = bot.save_decision_log(args.decision_log)
        print(f"\nDecision log written to {path}")

    if not args.no_plot:
        from .analysis import plot_run

        plot_run(engine, save_path=args.plot, show=args.plot is None)

    return 0


if __name__ == "__main__":
    sys.exit(main())
