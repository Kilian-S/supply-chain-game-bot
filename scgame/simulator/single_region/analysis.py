"""Charts summarising a completed Single-Region Run simulation."""

import matplotlib.pyplot as plt
import numpy as np

from ...common.economics import STOCKOUT_COST_PER_DRUM
from .engine import SingleRegionEngine


def plot_run(engine: SingleRegionEngine, save_path: str = None, show: bool = True):
    """Draw a four-panel summary of a completed run.

    The panels are cash over time, inventory split by where it is sitting,
    demand against what was actually sold, and cumulative lost demand. Read
    together they show whether inventory was built early enough to cover the
    seasonal peak, which is the single question the strategy exists to answer.
    """
    records = engine.daily_records
    if not records:
        raise ValueError("The simulation produced no records to plot.")

    days = [record.day for record in records]
    warehouse = [record.warehouse_inventory for record in records]
    work_in_progress = [record.work_in_progress for record in records]
    in_transit = [record.in_transit_inventory for record in records]
    demand = [record.demand for record in records]
    sales = [record.sales for record in records]
    stockouts = [record.stockout for record in records]
    cash = [record.cash for record in records]
    cumulative_lost = np.cumsum(stockouts)

    summary = engine.financial_summary()

    figure, axes = plt.subplots(2, 2, figsize=(14, 10))
    figure.suptitle(
        "Single-Region Run, simulated\n"
        f"Final cash ${summary['cash']:,.0f}   "
        f"Item fill rate {summary['item_fill_rate'] * 100:.2f}%   "
        f"Lost demand {summary['total_stockouts']:,} drums",
        fontsize=13,
        fontweight="bold",
    )

    cash_axis = axes[0, 0]
    cash_axis.plot(days, cash, color="#2e7d32", linewidth=1.5)
    cash_axis.fill_between(days, cash, alpha=0.25, color="#2e7d32")
    cash_axis.set_title("Cash balance")
    cash_axis.set_xlabel("Day")
    cash_axis.set_ylabel("Cash")
    cash_axis.grid(True, alpha=0.3)
    cash_axis.yaxis.set_major_formatter(
        plt.FuncFormatter(lambda value, _: f"${value / 1e6:.1f}M")
    )

    inventory_axis = axes[0, 1]
    inventory_axis.stackplot(
        days,
        warehouse,
        work_in_progress,
        in_transit,
        labels=["Warehouse", "In production", "In transit"],
        colors=["#2ecc71", "#f1c40f", "#3498db"],
        alpha=0.75,
    )
    inventory_axis.set_title("Where inventory is sitting")
    inventory_axis.set_xlabel("Day")
    inventory_axis.set_ylabel("Drums")
    inventory_axis.legend(loc="upper right")
    inventory_axis.grid(True, alpha=0.3)

    service_axis = axes[1, 0]
    service_axis.plot(days, demand, color="#34495e", alpha=0.6, linewidth=0.8,
                      label="Demand")
    service_axis.plot(days, sales, color="#2ecc71", alpha=0.9, linewidth=0.8,
                      label="Sold")
    service_axis.fill_between(
        days, sales, demand,
        where=[d > s for d, s in zip(demand, sales)],
        color="#e74c3c", alpha=0.35, label="Lost",
    )
    service_axis.set_title("Demand against sales")
    service_axis.set_xlabel("Day")
    service_axis.set_ylabel("Drums per day")
    service_axis.legend(loc="upper right")
    service_axis.grid(True, alpha=0.3)

    lost_axis = axes[1, 1]
    lost_axis.plot(days, cumulative_lost, color="#e74c3c", linewidth=2)
    lost_axis.fill_between(days, cumulative_lost, alpha=0.25, color="#e74c3c")
    lost_axis.set_title("Cumulative lost demand")
    lost_axis.set_xlabel("Day")
    lost_axis.set_ylabel("Drums")
    lost_axis.grid(True, alpha=0.3)
    lost_axis.annotate(
        f"Lost contribution ${summary['total_stockouts'] * STOCKOUT_COST_PER_DRUM:,.0f}",
        xy=(0.96, 0.94),
        xycoords="axes fraction",
        ha="right",
        va="top",
        fontsize=10,
        color="#c0392b",
        bbox=dict(boxstyle="round", facecolor="white", alpha=0.85),
    )

    figure.tight_layout()

    if save_path:
        figure.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"Analysis figure written to {save_path}")

    if show:
        plt.show()

    return figure
