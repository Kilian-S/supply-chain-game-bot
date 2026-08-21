# Post-simulation analysis for multi-region network simulator.
# Provides navigable screens with matplotlib for detailed analysis.

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from typing import List, Dict
from dataclasses import dataclass

from .engine import NetworkEngine
from ...network.config import NETWORK, ShippingMethod


# Cost constants for opportunity cost calculation
STOCKOUT_COST_PER_DRUM = 325  # Lost profit margin per stockout

# Region colours, kept consistent across every screen
REGION_COLORS = {
    'Calopeia': '#E24A33',    # Lobster (reddish)
    'Sorange': '#B19CD9',     # Light purple
    'Tyran': '#DAA520',       # Dark yellow (goldenrod)
    'Entworpe': '#008B8B',    # Dark turquoise (dark cyan)
    'Fardo': '#D2691E',       # Light brown (chocolate)
}

# Shipping method colours, chosen to contrast
SHIPPING_COLORS = {
    'TRUCK': '#2E86AB',       # Blue
    'MAIL': '#E94F37',        # Red
}

# Region to warehouse mapping
REGION_TO_WAREHOUSE = {
    'Calopeia': 'Calopeia_WH',
    'Sorange': 'Sorange_WH',
    'Tyran': 'Tyran_WH',
    'Entworpe': 'Calopeia_WH',  # Backup region
    'Fardo': 'Fardo_WH',
}

# Region to factory mapping (which factory serves the region's warehouse)
REGION_TO_FACTORY = {
    'Calopeia': 'Calopeia_Factory',
    'Sorange': 'Sorange_Factory',
    'Tyran': 'Calopeia_Factory',
    'Entworpe': 'Calopeia_Factory',
    'Fardo': 'Fardo_Factory',
}


@dataclass
class AnalysisData:
    """Extracted time-series data from simulation records."""
    days: List[int]
    cash: List[float]

    # Per-warehouse
    warehouse_inventory: Dict[str, List[int]]
    warehouse_in_transit: Dict[str, List[int]]
    warehouse_wip: Dict[str, List[int]]  # WIP destined for each warehouse

    # Per-factory
    factory_wip: Dict[str, List[int]]
    factory_capacity: Dict[str, List[int]]

    # Per-region
    regional_demand: Dict[str, List[int]]
    regional_fulfilled: Dict[str, List[int]]
    regional_stockout: Dict[str, List[int]]
    regional_cum_stockout: Dict[str, List[int]]

    # Financials
    revenue: List[float]
    production_cost: List[float]
    shipping_cost: List[float]
    truck_shipping_cost: List[float]
    mail_shipping_cost: List[float]
    fulfilment_cost: List[float]
    holding_cost: List[float]
    interest: List[float]

    # Cumulative financials
    cum_revenue: List[float]
    cum_production_cost: List[float]
    cum_shipping_cost: List[float]
    cum_truck_shipping_cost: List[float]
    cum_mail_shipping_cost: List[float]
    cum_fulfilment_cost: List[float]
    cum_holding_cost: List[float]
    cum_interest: List[float]

    # Shipments per route: route_key -> list of (day, quantity, method, cost)
    route_shipments: Dict[str, List[tuple]]

    # Cumulative shipping costs per warehouse (for regional analysis)
    cum_truck_cost_by_warehouse: Dict[str, List[float]]
    cum_mail_cost_by_warehouse: Dict[str, List[float]]

    # Per-route shipment costs (for detailed breakdown)
    route_truck_costs: Dict[str, List[tuple]]  # route_key -> [(day, cost), ...]
    route_mail_costs: Dict[str, List[tuple]]   # route_key -> [(day, cost), ...]


class NetworkAnalyser:
    """
    Multi-screen post-simulation analyser.

    Screens:
    1. Global Overview - Cash, profit breakdown, total stockouts
    2. Calopeia System - Factory, warehouses, regions
    3. Sorange System
    4. Fardo System
    5. Shipping Analysis - Mail vs truck per route
    6-0. Regional Details - Per-region 4-panel views

    Navigation:
    - Press 1-5, 6-0 to jump to screen
    - Left/Right arrows to cycle
    - Q to quit
    """

    SCREENS = [
        "Global Overview",
        "Calopeia System",
        "Sorange System",
        "Fardo System",
        "Shipping Analysis",
        "Calopeia Detail",
        "Sorange Detail",
        "Tyran Detail",
        "Entworpe Detail",
        "Fardo Detail",
        "Cross-Warehouse Fulfilment",
        "Total Inventory",
    ]

    # System definitions
    SYSTEMS = {
        "Calopeia": {
            "factory": "Calopeia_Factory",
            "warehouses": ["Calopeia_WH", "Tyran_WH"],
            "regions": ["Calopeia", "Tyran", "Entworpe"],
        },
        "Sorange": {
            "factory": "Sorange_Factory",
            "warehouses": ["Sorange_WH"],
            "regions": ["Sorange"],
        },
        "Fardo": {
            "factory": "Fardo_Factory",
            "warehouses": ["Fardo_WH"],
            "regions": ["Fardo"],
        },
    }

    def __init__(self, engine: NetworkEngine):
        self.engine = engine
        self.data = self._extract_data()
        self.current_screen = 0
        self.fig = None
        self.running = True

    def _extract_data(self) -> AnalysisData:
        """Extract time-series data from engine records."""
        records = self.engine.daily_records

        if not records:
            raise ValueError("No simulation records to analyse")

        days = [r.day for r in records]
        cash = [r.cash for r in records]

        # Initialise per-entity dicts
        warehouses = ["Calopeia_WH", "Sorange_WH", "Tyran_WH", "Fardo_WH"]
        factories = ["Calopeia_Factory", "Sorange_Factory", "Fardo_Factory"]
        regions = ["Calopeia", "Sorange", "Tyran", "Entworpe", "Fardo"]

        warehouse_inventory = {wh: [] for wh in warehouses}
        warehouse_in_transit = {wh: [] for wh in warehouses}
        warehouse_wip = {wh: [] for wh in warehouses}  # WIP destined for each warehouse
        factory_wip = {f: [] for f in factories}
        factory_capacity = {f: [] for f in factories}
        regional_demand = {r: [] for r in regions}
        regional_fulfilled = {r: [] for r in regions}
        regional_stockout = {r: [] for r in regions}
        regional_cum_stockout = {r: [] for r in regions}

        revenue = []
        production_cost = []
        shipping_cost = []
        truck_shipping_cost = []
        mail_shipping_cost = []
        fulfilment_cost = []
        holding_cost = []
        interest = []

        # Cumulative tracking
        cum_stockout = {r: 0 for r in regions}
        cum_rev = 0
        cum_prod = 0
        cum_ship = 0
        cum_truck_ship = 0
        cum_mail_ship = 0
        cum_fulfil = 0
        cum_hold = 0
        cum_int = 0

        cum_revenue = []
        cum_production_cost = []
        cum_shipping_cost = []
        cum_truck_shipping_cost = []
        cum_mail_shipping_cost = []
        cum_fulfilment_cost = []
        cum_holding_cost = []
        cum_interest = []

        # Shipments per route
        route_shipments = {}

        # Cumulative shipping costs per warehouse
        cum_truck_cost_by_warehouse = {wh: [] for wh in warehouses}
        cum_mail_cost_by_warehouse = {wh: [] for wh in warehouses}
        running_truck_by_warehouse = {wh: 0.0 for wh in warehouses}
        running_mail_by_warehouse = {wh: 0.0 for wh in warehouses}

        # Per-route costs for detailed tracking
        route_truck_costs = {}
        route_mail_costs = {}

        for r in records:
            # Warehouse data
            for wh in warehouses:
                warehouse_inventory[wh].append(r.warehouse_inventories.get(wh, 0))
                warehouse_in_transit[wh].append(r.warehouse_in_transit.get(wh, 0))
                # WIP destined for this warehouse (if available in record)
                wip_for_wh = r.warehouse_wip.get(wh, 0) if hasattr(r, 'warehouse_wip') and r.warehouse_wip else 0
                warehouse_wip[wh].append(wip_for_wh)

            # Factory data
            for f in factories:
                factory_wip[f].append(r.factory_wip.get(f, 0))
                factory_capacity[f].append(r.factory_capacities.get(f, 0))

            # Regional data
            for region in regions:
                demand = r.regional_demand.get(region, 0)
                result = r.fulfilment_results.get(region)
                fulfilled = result.fulfilled if result else 0
                stockout = result.stockout if result else 0

                regional_demand[region].append(demand)
                regional_fulfilled[region].append(fulfilled)
                regional_stockout[region].append(stockout)

                cum_stockout[region] += stockout
                regional_cum_stockout[region].append(cum_stockout[region])

            # Financials
            revenue.append(r.revenue)
            production_cost.append(r.production_cost)
            shipping_cost.append(r.shipping_cost)
            truck_shipping_cost.append(r.truck_shipping_cost)
            mail_shipping_cost.append(r.mail_shipping_cost)
            fulfilment_cost.append(r.fulfilment_cost)
            holding_cost.append(r.holding_cost)
            interest.append(r.interest)

            cum_rev += r.revenue
            cum_prod += r.production_cost
            cum_ship += r.shipping_cost
            cum_truck_ship += r.truck_shipping_cost
            cum_mail_ship += r.mail_shipping_cost
            cum_fulfil += r.fulfilment_cost
            cum_hold += r.holding_cost
            cum_int += r.interest

            cum_revenue.append(cum_rev)
            cum_production_cost.append(cum_prod)
            cum_shipping_cost.append(cum_ship)
            cum_truck_shipping_cost.append(cum_truck_ship)
            cum_mail_shipping_cost.append(cum_mail_ship)
            cum_fulfilment_cost.append(cum_fulfil)
            cum_holding_cost.append(cum_hold)
            cum_interest.append(cum_int)

            # Shipments - calculate per-shipment costs using network config
            for (factory, warehouse, qty, method) in r.shipments:
                route_key = f"{factory} → {warehouse}"
                if route_key not in route_shipments:
                    route_shipments[route_key] = []
                route_shipments[route_key].append((r.day, qty, method))

                # Calculate actual cost for this shipment using NETWORK
                ship_method = ShippingMethod.MAIL if method == 'MAIL' else ShippingMethod.TRUCK
                try:
                    cost = NETWORK.get_shipping_cost(factory, warehouse, ship_method, qty)
                except KeyError:
                    cost = 0  # Route not in config

                # Track per-route costs
                if method == 'TRUCK':
                    if route_key not in route_truck_costs:
                        route_truck_costs[route_key] = []
                    route_truck_costs[route_key].append((r.day, cost))
                    running_truck_by_warehouse[warehouse] += cost
                else:
                    if route_key not in route_mail_costs:
                        route_mail_costs[route_key] = []
                    route_mail_costs[route_key].append((r.day, cost))
                    running_mail_by_warehouse[warehouse] += cost

            # Record cumulative costs per warehouse for this day
            for wh in warehouses:
                cum_truck_cost_by_warehouse[wh].append(running_truck_by_warehouse[wh])
                cum_mail_cost_by_warehouse[wh].append(running_mail_by_warehouse[wh])

        return AnalysisData(
            days=days,
            cash=cash,
            warehouse_inventory=warehouse_inventory,
            warehouse_in_transit=warehouse_in_transit,
            warehouse_wip=warehouse_wip,
            factory_wip=factory_wip,
            factory_capacity=factory_capacity,
            regional_demand=regional_demand,
            regional_fulfilled=regional_fulfilled,
            regional_stockout=regional_stockout,
            regional_cum_stockout=regional_cum_stockout,
            revenue=revenue,
            production_cost=production_cost,
            shipping_cost=shipping_cost,
            truck_shipping_cost=truck_shipping_cost,
            mail_shipping_cost=mail_shipping_cost,
            fulfilment_cost=fulfilment_cost,
            holding_cost=holding_cost,
            interest=interest,
            cum_revenue=cum_revenue,
            cum_production_cost=cum_production_cost,
            cum_shipping_cost=cum_shipping_cost,
            cum_truck_shipping_cost=cum_truck_shipping_cost,
            cum_mail_shipping_cost=cum_mail_shipping_cost,
            cum_fulfilment_cost=cum_fulfilment_cost,
            cum_holding_cost=cum_holding_cost,
            cum_interest=cum_interest,
            route_shipments=route_shipments,
            cum_truck_cost_by_warehouse=cum_truck_cost_by_warehouse,
            cum_mail_cost_by_warehouse=cum_mail_cost_by_warehouse,
            route_truck_costs=route_truck_costs,
            route_mail_costs=route_mail_costs,
        )

    def _fmt_money(self, val: float) -> str:
        """Format currency value."""
        if abs(val) >= 1_000_000:
            return f"${val/1_000_000:.2f}M"
        elif abs(val) >= 1_000:
            return f"${val/1_000:.1f}K"
        else:
            return f"${val:.0f}"

    def _on_key(self, event):
        """Handle keyboard navigation."""
        if event.key == 'q':
            self.running = False
            plt.close(self.fig)
        elif event.key == 'right':
            self.current_screen = (self.current_screen + 1) % len(self.SCREENS)
            self._draw_current_screen()
        elif event.key == 'left':
            self.current_screen = (self.current_screen - 1) % len(self.SCREENS)
            self._draw_current_screen()
        elif event.key in '12345':
            self.current_screen = int(event.key) - 1
            self._draw_current_screen()
        elif event.key in '67890':
            # 6->5, 7->6, 8->7, 9->8, 0->9
            if event.key == '0':
                self.current_screen = 9
            else:
                self.current_screen = int(event.key) - 1
            self._draw_current_screen()
        elif event.key == '-':
            # Cross-warehouse fulfilment screen
            self.current_screen = 10
            self._draw_current_screen()
        elif event.key == '=':
            # Total inventory screen
            self.current_screen = 11
            self._draw_current_screen()

    def _draw_current_screen(self):
        """Draw the current screen."""
        self.fig.clear()

        screen_methods = [
            self._draw_global_overview,
            self._draw_calopeia_system,
            self._draw_sorange_system,
            self._draw_fardo_system,
            self._draw_shipping_analysis,
            lambda: self._draw_region_detail("Calopeia"),
            lambda: self._draw_region_detail("Sorange"),
            lambda: self._draw_region_detail("Tyran"),
            lambda: self._draw_region_detail("Entworpe"),
            lambda: self._draw_region_detail("Fardo"),
            self._draw_cross_fulfilment,
            self._draw_total_inventory,
        ]

        screen_methods[self.current_screen]()

        # Add navigation footer
        nav_text = "Navigation: [1-5] Systems | [6-0] Regions | [-] Cross-WH | [=] Inventory | [←/→] Cycle | [Q] Quit"
        screen_text = f"Screen {self.current_screen + 1}/{len(self.SCREENS)}: {self.SCREENS[self.current_screen]}"
        self.fig.text(0.5, 0.02, nav_text, ha='center', fontsize=9, color='gray')
        self.fig.text(0.5, 0.98, screen_text, ha='center', fontsize=11, fontweight='bold')

        self.fig.canvas.draw()

    # === Screen 1: Global Overview ===

    def _draw_global_overview(self):
        """Draw global overview screen."""
        axes = self.fig.subplots(2, 2)

        d = self.data
        summary = self.engine.get_financial_summary()

        # 1. Cash balance over time (top-left)
        ax1 = axes[0, 0]
        ax1.plot(d.days, d.cash, color='green', linewidth=1.5)
        ax1.fill_between(d.days, d.cash, alpha=0.3, color='green')
        ax1.axhline(y=0, color='red', linestyle='--', alpha=0.5)
        ax1.set_title('Cash Balance Over Time')
        ax1.set_xlabel('Day')
        ax1.set_ylabel('Cash ($)')
        ax1.grid(True, alpha=0.3)
        ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'${x/1e6:.1f}M'))

        # 2. Profit breakdown with split production and shipping costs (top-right)
        ax2 = axes[0, 1]
        categories = ['Revenue', 'Fixed Prod', 'Var Prod', 'Truck Ship', 'Mail Ship', 'Fulfil', 'Holding', 'CapEx', 'Interest']
        values = [
            summary['total_revenue'],
            -summary['total_fixed_production_cost'],
            -summary['total_variable_production_cost'],
            -summary['total_truck_shipping_cost'],
            -summary['total_mail_shipping_cost'],
            -summary['total_fulfilment_cost'],
            -summary['total_holding_cost'],
            -summary['total_capex'],
            summary['total_interest'],
        ]
        colors = ['green' if v >= 0 else 'red' for v in values]
        bars = ax2.bar(categories, [abs(v)/1e6 for v in values], color=colors, alpha=0.7)
        ax2.set_title(f'Profit Breakdown (Total: {self._fmt_money(summary["total_profit"])})')
        ax2.set_ylabel('Amount ($M)')
        ax2.tick_params(axis='x', rotation=45)

        # Add value labels
        for bar, val in zip(bars, values):
            sign = '+' if val >= 0 else '-'
            ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height(),
                    f'{sign}${abs(val)/1e6:.1f}M', ha='center', va='bottom', fontsize=7)

        # 3. Total stockouts by region with region colors (bottom-left)
        ax3 = axes[1, 0]
        regions = ["Calopeia", "Sorange", "Tyran", "Entworpe", "Fardo"]
        final_stockouts = [d.regional_cum_stockout[r][-1] if d.regional_cum_stockout[r] else 0 for r in regions]
        colors = [REGION_COLORS[r] for r in regions]
        ax3.bar(regions, final_stockouts, color=colors)
        ax3.set_title(f'Total Stockouts by Region (Total: {sum(final_stockouts):,})')
        ax3.set_ylabel('Drums')
        ax3.tick_params(axis='x', rotation=45)

        for i, v in enumerate(final_stockouts):
            ax3.text(i, v, f'{v:,}', ha='center', va='bottom', fontsize=9)

        # 4. Cumulative stockouts over time with region colors (bottom-right)
        ax4 = axes[1, 1]
        for region in regions:
            ax4.plot(d.days, d.regional_cum_stockout[region],
                    label=region, linewidth=1.5, color=REGION_COLORS[region])
        ax4.set_title('Cumulative Stockouts Over Time')
        ax4.set_xlabel('Day')
        ax4.set_ylabel('Total Drums Lost')
        ax4.legend(loc='upper left', fontsize=8)
        ax4.grid(True, alpha=0.3)

        self.fig.tight_layout(rect=[0, 0.03, 1, 0.95])

    # === Screen 2-4: System Screens ===

    def _draw_system_screen(self, system_name: str):
        """Draw a system-specific screen."""
        system = self.SYSTEMS[system_name]
        factory = system["factory"]
        warehouses = system["warehouses"]
        regions = system["regions"]

        axes = self.fig.subplots(2, 2)
        d = self.data

        # 1. Warehouse inventory (top-left)
        ax1 = axes[0, 0]
        for wh in warehouses:
            total = [inv + transit for inv, transit in zip(d.warehouse_inventory[wh], d.warehouse_in_transit[wh])]
            ax1.plot(d.days, d.warehouse_inventory[wh], label=f'{wh} (on-hand)', linewidth=1.5)
            ax1.plot(d.days, total, label=f'{wh} (total)', linestyle='--', alpha=0.7)
        ax1.set_title('Warehouse Inventory')
        ax1.set_xlabel('Day')
        ax1.set_ylabel('Drums')
        ax1.legend(loc='upper right', fontsize=8)
        ax1.grid(True, alpha=0.3)

        # 2. Factory WIP and capacity (top-right)
        ax2 = axes[0, 1]
        ax2.plot(d.days, d.factory_wip[factory], label='WIP', color='orange', linewidth=1.5)
        ax2.plot(d.days, d.factory_capacity[factory], label='Capacity', color='blue', linestyle='--')
        ax2.set_title(f'{factory} Production')
        ax2.set_xlabel('Day')
        ax2.set_ylabel('Drums / Capacity')
        ax2.legend(loc='upper right')
        ax2.grid(True, alpha=0.3)

        # 3. Demand vs fulfilled with region colors (bottom-left)
        ax3 = axes[1, 0]
        for region in regions:
            color = REGION_COLORS[region]
            ax3.plot(d.days, d.regional_demand[region], label=f'{region} demand', color=color, alpha=0.7)
            ax3.plot(d.days, d.regional_fulfilled[region], label=f'{region} fulfilled', color=color, linestyle='--')
        ax3.set_title('Demand vs Fulfilled')
        ax3.set_xlabel('Day')
        ax3.set_ylabel('Drums')
        ax3.legend(loc='upper right', fontsize=7)
        ax3.grid(True, alpha=0.3)

        # 4. Cumulative stockouts with region colors (bottom-right)
        ax4 = axes[1, 1]
        for region in regions:
            color = REGION_COLORS[region]
            ax4.plot(d.days, d.regional_cum_stockout[region], label=region, linewidth=1.5, color=color)
            ax4.fill_between(d.days, d.regional_cum_stockout[region], alpha=0.2, color=color)
        ax4.set_title('Cumulative Stockouts')
        ax4.set_xlabel('Day')
        ax4.set_ylabel('Total Drums Lost')
        ax4.legend(loc='upper left')
        ax4.grid(True, alpha=0.3)

        # Add summary stats
        total_stockout = sum(d.regional_cum_stockout[r][-1] for r in regions if d.regional_cum_stockout[r])
        opp_cost = total_stockout * STOCKOUT_COST_PER_DRUM
        ax4.annotate(
            f'Total: {total_stockout:,} drums\nOpp. Cost: ${opp_cost:,.0f}',
            xy=(0.95, 0.95), xycoords='axes fraction',
            ha='right', va='top', fontsize=9,
            bbox=dict(boxstyle='round', facecolor='white', alpha=0.8)
        )

        self.fig.tight_layout(rect=[0, 0.03, 1, 0.95])

    def _draw_calopeia_system(self):
        self._draw_system_screen("Calopeia")

    def _draw_sorange_system(self):
        self._draw_system_screen("Sorange")

    def _draw_fardo_system(self):
        self._draw_system_screen("Fardo")

    # === Screen 5: Shipping Analysis ===

    def _draw_shipping_analysis(self):
        """
        Draw shipping cost analysis.

        Layout:
        - Top half: Two large cumulative line graphs
          - Left: Cumulative truck costs over time
          - Right: Cumulative mail costs over time
        - Bottom half: 8 small graphs (2 rows × 4 columns)
          - Each column = one warehouse (Calopeia, Sorange, Tyran, Fardo)
          - Upper row: Cumulative mail costs per warehouse
          - Lower row: Cumulative truck costs per warehouse
        """
        d = self.data

        # Create grid: top row has 2 large plots, bottom has 2 rows of 4
        from matplotlib.gridspec import GridSpec
        gs = GridSpec(4, 4, figure=self.fig, height_ratios=[2, 2, 1, 1], hspace=0.4, wspace=0.3)

        # Top-left: Cumulative truck costs over time
        ax_truck_total = self.fig.add_subplot(gs[0:2, 0:2])
        ax_truck_total.plot(d.days, d.cum_truck_shipping_cost, color=SHIPPING_COLORS['TRUCK'], linewidth=2)
        ax_truck_total.fill_between(d.days, d.cum_truck_shipping_cost, alpha=0.3, color=SHIPPING_COLORS['TRUCK'])
        ax_truck_total.set_title('Cumulative Truck Shipping Costs', fontsize=11, fontweight='bold')
        ax_truck_total.set_xlabel('Day')
        ax_truck_total.set_ylabel('Cost ($)')
        ax_truck_total.grid(True, alpha=0.3)
        ax_truck_total.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'${x/1e6:.2f}M' if x >= 1e6 else f'${x/1e3:.0f}K'))

        # Add final value annotation
        final_truck = d.cum_truck_shipping_cost[-1] if d.cum_truck_shipping_cost else 0
        ax_truck_total.annotate(
            f'Total: {self._fmt_money(final_truck)}',
            xy=(0.95, 0.95), xycoords='axes fraction',
            ha='right', va='top', fontsize=10,
            bbox=dict(boxstyle='round', facecolor='white', alpha=0.8)
        )

        # Top-right: Cumulative mail costs over time
        ax_mail_total = self.fig.add_subplot(gs[0:2, 2:4])
        ax_mail_total.plot(d.days, d.cum_mail_shipping_cost, color=SHIPPING_COLORS['MAIL'], linewidth=2)
        ax_mail_total.fill_between(d.days, d.cum_mail_shipping_cost, alpha=0.3, color=SHIPPING_COLORS['MAIL'])
        ax_mail_total.set_title('Cumulative Mail Shipping Costs', fontsize=11, fontweight='bold')
        ax_mail_total.set_xlabel('Day')
        ax_mail_total.set_ylabel('Cost ($)')
        ax_mail_total.grid(True, alpha=0.3)
        ax_mail_total.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'${x/1e6:.2f}M' if x >= 1e6 else f'${x/1e3:.0f}K'))

        # Add final value annotation
        final_mail = d.cum_mail_shipping_cost[-1] if d.cum_mail_shipping_cost else 0
        ax_mail_total.annotate(
            f'Total: {self._fmt_money(final_mail)}',
            xy=(0.95, 0.95), xycoords='axes fraction',
            ha='right', va='top', fontsize=10,
            bbox=dict(boxstyle='round', facecolor='white', alpha=0.8)
        )

        # Warehouses for bottom section (4 columns)
        warehouses = [
            ('Calopeia', 'Calopeia_WH'),
            ('Sorange', 'Sorange_WH'),
            ('Tyran', 'Tyran_WH'),
            ('Fardo', 'Fardo_WH'),
        ]

        # Bottom section: 2 rows × 4 columns
        # Row 2 (index 2): Mail costs per warehouse
        # Row 3 (index 3): Truck costs per warehouse

        for col, (region, wh) in enumerate(warehouses):
            color = REGION_COLORS[region]

            # Mail costs (row 2)
            ax_mail = self.fig.add_subplot(gs[2, col])
            if wh in d.cum_mail_cost_by_warehouse:
                mail_data = d.cum_mail_cost_by_warehouse[wh]
                ax_mail.plot(d.days, mail_data, color=SHIPPING_COLORS['MAIL'], linewidth=1)
                ax_mail.fill_between(d.days, mail_data, alpha=0.3, color=SHIPPING_COLORS['MAIL'])
                final_val = mail_data[-1] if mail_data else 0
                ax_mail.set_title(f'{region}\nMail: {self._fmt_money(final_val)}', fontsize=8, color=color)
            else:
                ax_mail.set_title(f'{region}\nMail: $0', fontsize=8, color=color)
            ax_mail.set_xticks([])
            ax_mail.tick_params(axis='y', labelsize=6)
            ax_mail.grid(True, alpha=0.2)

            # Truck costs (row 3)
            ax_truck = self.fig.add_subplot(gs[3, col])
            if wh in d.cum_truck_cost_by_warehouse:
                truck_data = d.cum_truck_cost_by_warehouse[wh]
                ax_truck.plot(d.days, truck_data, color=SHIPPING_COLORS['TRUCK'], linewidth=1)
                ax_truck.fill_between(d.days, truck_data, alpha=0.3, color=SHIPPING_COLORS['TRUCK'])
                final_val = truck_data[-1] if truck_data else 0
                ax_truck.set_title(f'Truck: {self._fmt_money(final_val)}', fontsize=8)
            else:
                ax_truck.set_title('Truck: $0', fontsize=8)
            ax_truck.set_xticks([])
            ax_truck.tick_params(axis='y', labelsize=6)
            ax_truck.grid(True, alpha=0.2)

        self.fig.tight_layout(rect=[0, 0.03, 1, 0.95])

    # === Screens 6-10: Regional Detail Screens ===

    def _draw_region_detail(self, region: str):
        """Draw detailed view for a single region."""
        axes = self.fig.subplots(2, 2)
        d = self.data

        warehouse = REGION_TO_WAREHOUSE[region]
        factory = REGION_TO_FACTORY[region]
        color = REGION_COLORS[region]

        # Get route key for shipments
        route_key = f"{factory} → {warehouse}"

        # 1. Shipping method timeline (top-left)
        ax1 = axes[0, 0]
        if route_key in d.route_shipments:
            shipments = d.route_shipments[route_key]
            # Create background showing shipping method over time
            for day, qty, method in shipments:
                ship_color = SHIPPING_COLORS.get(method, 'gray')
                ax1.axvline(x=day, color=ship_color, alpha=0.7, linewidth=2)

            # Add legend
            legend_elements = [
                mpatches.Patch(facecolor=SHIPPING_COLORS['TRUCK'], label='Truck'),
                mpatches.Patch(facecolor=SHIPPING_COLORS['MAIL'], label='Mail'),
            ]
            ax1.legend(handles=legend_elements, loc='upper right', fontsize=8)

            truck_count = sum(1 for _, _, m in shipments if m == 'TRUCK')
            mail_count = sum(1 for _, _, m in shipments if m == 'MAIL')
            ax1.set_title(f'Shipping to {warehouse} (Truck: {truck_count}, Mail: {mail_count})')
        else:
            ax1.set_title(f'Shipping to {warehouse} (No shipments)')
            ax1.text(0.5, 0.5, 'No shipments recorded', ha='center', va='center', transform=ax1.transAxes)

        ax1.set_xlabel('Day')
        ax1.set_ylabel('Shipment Events')
        ax1.set_xlim(d.days[0], d.days[-1])
        ax1.grid(True, alpha=0.3)

        # 2. Inventory stacked: warehouse + in-transit + WIP destined for this warehouse (top-right)
        ax2 = axes[0, 1]
        wh_inv = d.warehouse_inventory[warehouse]
        wh_transit = d.warehouse_in_transit[warehouse]
        wh_wip = d.warehouse_wip[warehouse]  # WIP destined for THIS warehouse only

        ax2.stackplot(
            d.days, wh_inv, wh_transit, wh_wip,
            labels=['Warehouse', 'In Transit', 'WIP (for this WH)'],
            colors=['#2ecc71', '#3498db', '#f1c40f'],
            alpha=0.7
        )
        ax2.set_title(f'{region} Inventory Pipeline')
        ax2.set_xlabel('Day')
        ax2.set_ylabel('Drums')
        ax2.legend(loc='upper right', fontsize=8)
        ax2.grid(True, alpha=0.3)

        # 3. Cumulative lost sales (bottom-left)
        ax3 = axes[1, 0]
        ax3.plot(d.days, d.regional_cum_stockout[region], color=color, linewidth=2)
        ax3.fill_between(d.days, d.regional_cum_stockout[region], alpha=0.3, color=color)
        ax3.set_title(f'{region} Cumulative Lost Sales')
        ax3.set_xlabel('Day')
        ax3.set_ylabel('Total Drums Lost')
        ax3.grid(True, alpha=0.3)

        # Add opportunity cost annotation
        total_lost = d.regional_cum_stockout[region][-1] if d.regional_cum_stockout[region] else 0
        lost_revenue = total_lost * STOCKOUT_COST_PER_DRUM
        ax3.annotate(
            f'Total: {total_lost:,} drums\nOpp. Cost: ${lost_revenue:,.0f}',
            xy=(0.95, 0.95), xycoords='axes fraction',
            ha='right', va='top', fontsize=10, color=color,
            bbox=dict(boxstyle='round', facecolor='white', alpha=0.8)
        )

        # 4. Demand vs fulfilled (bottom-right)
        ax4 = axes[1, 1]
        ax4.plot(d.days, d.regional_demand[region], color='blue', alpha=0.7, label='Demand', linewidth=1)
        ax4.plot(d.days, d.regional_fulfilled[region], color='green', alpha=0.7, label='Fulfilled', linewidth=1)
        ax4.fill_between(
            d.days, d.regional_fulfilled[region], d.regional_demand[region],
            where=[dem > ful for dem, ful in zip(d.regional_demand[region], d.regional_fulfilled[region])],
            color='red', alpha=0.3, label='Lost Sales'
        )
        ax4.set_title(f'{region} Demand vs Fulfilled')
        ax4.set_xlabel('Day')
        ax4.set_ylabel('Drums')
        ax4.legend(loc='upper right', fontsize=8)
        ax4.grid(True, alpha=0.3)

        self.fig.tight_layout(rect=[0, 0.03, 1, 0.95])

    # === Screen 11: Cross-Warehouse Fulfilment ===

    def _draw_cross_fulfilment(self):
        """
        Draw cross-warehouse fulfilment analysis.

        Layout:
        - Top half: 5 pie charts (one per region) showing warehouse breakdown
        - Bottom left: Timeline of cross-fulfilment events
        - Bottom right: Summary statistics panel
        """
        from matplotlib.gridspec import GridSpec
        from collections import defaultdict

        # Extract cross-fulfilment data from daily records
        regions = ["Calopeia", "Sorange", "Tyran", "Entworpe", "Fardo"]
        warehouses = ["Calopeia_WH", "Sorange_WH", "Tyran_WH", "Fardo_WH"]

        # Primary warehouse for each region (normal fulfilment)
        primary_warehouse = {
            "Calopeia": "Calopeia_WH",
            "Sorange": "Sorange_WH",
            "Tyran": "Tyran_WH",
            "Entworpe": "Tyran_WH",  # Entworpe served by Tyran_WH
            "Fardo": "Fardo_WH",
        }

        # Aggregate fulfilment by region and source warehouse
        # region -> warehouse -> total drums
        fulfilment_by_region = {r: defaultdict(int) for r in regions}

        # Timeline data: (day, region, warehouse, quantity) for cross-fulfilment only
        cross_fulfilment_events = []

        for record in self.engine.daily_records:
            for region, result in record.fulfilment_results.items():
                for detail in result.fulfilment_details:
                    wh = detail['warehouse']
                    qty = detail['quantity']
                    fulfilment_by_region[region][wh] += qty

                    # Track cross-fulfilment events
                    if wh != primary_warehouse.get(region):
                        cross_fulfilment_events.append({
                            'day': record.day,
                            'region': region,
                            'warehouse': wh,
                            'quantity': qty,
                            'cost_per_drum': detail['cost_per_drum'],
                        })

        # Create grid layout
        gs = GridSpec(2, 5, figure=self.fig, height_ratios=[1, 1], hspace=0.3, wspace=0.3)

        # Warehouse colors for pie charts
        wh_colors = {
            'Calopeia_WH': '#E24A33',
            'Sorange_WH': '#B19CD9',
            'Tyran_WH': '#DAA520',
            'Fardo_WH': '#D2691E',
        }

        # === Top row: 5 pie charts ===
        for i, region in enumerate(regions):
            ax = self.fig.add_subplot(gs[0, i])

            region_data = fulfilment_by_region[region]
            if not region_data or sum(region_data.values()) == 0:
                ax.text(0.5, 0.5, 'No data', ha='center', va='center', transform=ax.transAxes)
                ax.set_title(region, fontsize=10, fontweight='bold', color=REGION_COLORS[region])
                ax.axis('off')
                continue

            # Prepare pie data
            labels = []
            sizes = []
            colors = []
            explode = []

            primary_wh = primary_warehouse[region]
            for wh in warehouses:
                qty = region_data.get(wh, 0)
                if qty > 0:
                    labels.append(wh.replace('_WH', ''))
                    sizes.append(qty)
                    colors.append(wh_colors[wh])
                    # Explode cross-fulfilment slices
                    explode.append(0.05 if wh != primary_wh else 0)

            if sizes:
                wedges, texts, autotexts = ax.pie(
                    sizes, labels=None, colors=colors, explode=explode,
                    autopct=lambda pct: f'{pct:.0f}%' if pct > 5 else '',
                    pctdistance=0.75, startangle=90
                )
                # Add legend for this pie
                ax.legend(wedges, labels, loc='lower center', fontsize=6,
                         bbox_to_anchor=(0.5, -0.15), ncol=2)

            # Calculate cross-fulfilment percentage
            total = sum(sizes)
            primary_qty = region_data.get(primary_wh, 0)
            cross_pct = ((total - primary_qty) / total * 100) if total > 0 else 0

            title_color = REGION_COLORS[region]
            ax.set_title(f'{region}\n({cross_pct:.1f}% cross)', fontsize=9,
                        fontweight='bold', color=title_color)

        # === Bottom left: Timeline ===
        ax_timeline = self.fig.add_subplot(gs[1, 0:3])

        if cross_fulfilment_events:
            # Group by day and sum quantities
            daily_cross = defaultdict(int)
            for evt in cross_fulfilment_events:
                daily_cross[evt['day']] += evt['quantity']

            days = sorted(daily_cross.keys())
            quantities = [daily_cross[d] for d in days]

            # Scatter plot with size based on quantity
            ax_timeline.scatter(days, [1] * len(days), s=[q * 2 for q in quantities],
                               alpha=0.6, c='red', label='Cross-fulfilment')

            # Add cumulative line
            ax_timeline_twin = ax_timeline.twinx()
            cum_qty = []
            running = 0
            for d in self.data.days:
                running += daily_cross.get(d, 0)
                cum_qty.append(running)
            ax_timeline_twin.plot(self.data.days, cum_qty, color='darkred',
                                 linewidth=1.5, label='Cumulative')
            ax_timeline_twin.set_ylabel('Cumulative Drums', fontsize=8)
            ax_timeline_twin.tick_params(axis='y', labelsize=7)

            ax_timeline.set_xlim(self.data.days[0], self.data.days[-1])
            ax_timeline.set_ylim(0.5, 1.5)
            ax_timeline.set_yticks([])
            ax_timeline.set_xlabel('Day', fontsize=9)
            ax_timeline.set_title('Cross-Fulfilment Timeline (bubble size = quantity)',
                                 fontsize=10, fontweight='bold')
            ax_timeline.grid(True, alpha=0.3, axis='x')
        else:
            ax_timeline.text(0.5, 0.5, 'No cross-fulfilment events',
                            ha='center', va='center', transform=ax_timeline.transAxes, fontsize=12)
            ax_timeline.set_title('Cross-Fulfilment Timeline', fontsize=10, fontweight='bold')
            ax_timeline.axis('off')

        # === Bottom right: Summary stats ===
        ax_stats = self.fig.add_subplot(gs[1, 3:5])
        ax_stats.axis('off')

        # Calculate summary statistics
        total_cross_drums = sum(evt['quantity'] for evt in cross_fulfilment_events)
        total_cross_events = len(cross_fulfilment_events)

        # Calculate extra cost (cross costs $200 vs $150 normal = $50 extra per drum)
        extra_cost = sum(
            evt['quantity'] * (evt['cost_per_drum'] - 150)
            for evt in cross_fulfilment_events
        )

        # Total fulfilled across all regions
        total_fulfilled = sum(
            sum(fulfilment_by_region[r].values())
            for r in regions
        )
        cross_pct_overall = (total_cross_drums / total_fulfilled * 100) if total_fulfilled > 0 else 0

        # Breakdown by backup relationship
        backup_flows = defaultdict(int)
        for evt in cross_fulfilment_events:
            flow_key = f"{evt['warehouse'].replace('_WH', '')} → {evt['region']}"
            backup_flows[flow_key] += evt['quantity']

        # Build stats text
        stats_lines = [
            "CROSS-WAREHOUSE SUMMARY",
            "─" * 30,
            f"Total Cross-Fulfilled:  {total_cross_drums:,} drums",
            f"Cross-Fulfilment Rate: {cross_pct_overall:.2f}%",
            f"Number of Events:       {total_cross_events:,}",
            f"Extra Cost Incurred:    ${extra_cost:,.0f}",
            "",
            "TOP BACKUP FLOWS:",
            "─" * 30,
        ]

        # Add top 5 backup flows
        sorted_flows = sorted(backup_flows.items(), key=lambda x: -x[1])[:5]
        for flow, qty in sorted_flows:
            stats_lines.append(f"  {flow}: {qty:,} drums")

        if not sorted_flows:
            stats_lines.append("  (none)")

        stats_text = "\n".join(stats_lines)
        ax_stats.text(0.05, 0.95, stats_text, transform=ax_stats.transAxes,
                     fontsize=9, fontfamily='monospace', verticalalignment='top')

        self.fig.tight_layout(rect=[0, 0.03, 1, 0.95])

    # === Screen 12: Total Inventory ===

    def _draw_total_inventory(self):
        """
        Draw total inventory and cumulative demand.

        Top: Total inventory across all warehouses as stacked area chart.
        Bottom: Cumulative daily demand across all regions.
        """
        d = self.data
        axes = self.fig.subplots(2, 1)

        # === TOP: Total Inventory Stacked Area ===
        ax1 = axes[0]

        # Warehouse order and colors (consistent with REGION_COLORS)
        warehouses = [
            ('Calopeia_WH', 'Calopeia', '#E24A33'),
            ('Sorange_WH', 'Sorange', '#B19CD9'),
            ('Tyran_WH', 'Tyran', '#DAA520'),
            ('Fardo_WH', 'Fardo', '#D2691E'),
        ]

        # Prepare data arrays for stackplot
        inv_data = []
        labels = []
        colors = []

        for wh, label, color in warehouses:
            inv_data.append(d.warehouse_inventory[wh])
            labels.append(label)
            colors.append(color)

        # Calculate total for annotation
        total_inv = [sum(vals) for vals in zip(*inv_data)]

        # Stacked area chart
        ax1.stackplot(d.days, *inv_data, labels=labels, colors=colors, alpha=0.8)

        # Add a line at the top showing total
        ax1.plot(d.days, total_inv, color='black', linewidth=1.5, linestyle='-', alpha=0.7)

        # Formatting
        ax1.set_title('Total Inventory Across All Warehouses', fontsize=12, fontweight='bold')
        ax1.set_xlabel('Day', fontsize=10)
        ax1.set_ylabel('Inventory (Drums)', fontsize=10)
        ax1.grid(True, alpha=0.3, axis='y')
        ax1.set_xlim(d.days[0], d.days[-1])
        ax1.set_ylim(0, None)
        ax1.legend(loc='upper right', fontsize=9)

        # Add annotations for key stats
        max_inv = max(total_inv)
        max_day = d.days[total_inv.index(max_inv)]
        final_inv = total_inv[-1]

        stats_text = f"Peak: {max_inv:,} (Day {max_day}) | Final: {final_inv:,}"
        ax1.annotate(
            stats_text,
            xy=(0.02, 0.95), xycoords='axes fraction',
            ha='left', va='top', fontsize=9,
            bbox=dict(boxstyle='round', facecolor='white', alpha=0.9)
        )

        # === BOTTOM: Daily Demand (summed across regions) ===
        ax2 = axes[1]

        # Regions and colors
        regions = [
            ('Calopeia', '#E24A33'),
            ('Sorange', '#B19CD9'),
            ('Tyran', '#DAA520'),
            ('Entworpe', '#008B8B'),
            ('Fardo', '#D2691E'),
        ]

        # Get daily demand per region
        demand_data = []
        region_labels = []
        region_colors = []

        for region, color in regions:
            demand_data.append(d.regional_demand[region])
            region_labels.append(region)
            region_colors.append(color)

        # Calculate total daily demand
        total_daily_demand = [sum(vals) for vals in zip(*demand_data)]

        # Stacked area chart for daily demand
        ax2.stackplot(d.days, *demand_data, labels=region_labels, colors=region_colors, alpha=0.8)

        # Add a line at the top showing total
        ax2.plot(d.days, total_daily_demand, color='black', linewidth=1.5, linestyle='-', alpha=0.7)

        # Formatting
        ax2.set_title('Daily Demand Across All Regions', fontsize=12, fontweight='bold')
        ax2.set_xlabel('Day', fontsize=10)
        ax2.set_ylabel('Daily Demand (Drums)', fontsize=10)
        ax2.grid(True, alpha=0.3, axis='y')
        ax2.set_xlim(d.days[0], d.days[-1])
        ax2.set_ylim(0, None)
        ax2.legend(loc='upper right', fontsize=9)

        # Add stats annotation
        avg_demand = sum(total_daily_demand) / len(total_daily_demand) if total_daily_demand else 0
        max_demand = max(total_daily_demand) if total_daily_demand else 0
        total_demand = sum(total_daily_demand)
        ax2.annotate(
            f"Avg: {avg_demand:.0f}/day | Peak: {max_demand:,} | Total: {total_demand:,}",
            xy=(0.02, 0.95), xycoords='axes fraction',
            ha='left', va='top', fontsize=9,
            bbox=dict(boxstyle='round', facecolor='white', alpha=0.9)
        )

        self.fig.tight_layout(rect=[0, 0.03, 1, 0.95])

    # === Main Entry Point ===

    def show(self):
        """Open the analysis screens, navigable with the number and arrow keys."""
        self.fig = plt.figure(figsize=(14, 10))
        self.fig.canvas.mpl_connect('key_press_event', self._on_key)

        self._draw_current_screen()

        plt.show()

    def save_screen(self, path: str, screen: int = 0):
        """Render one analysis screen to an image file without opening a window.

        Screen 0 is the network overview. The remaining screens cover each
        system, shipping, each region, cross-fulfilment, and total inventory.
        """
        self.current_screen = screen
        self.fig = plt.figure(figsize=(14, 10))
        self._draw_current_screen()
        self.fig.savefig(path, dpi=150, bbox_inches='tight')
        plt.close(self.fig)
        self.fig = None
        return path

    def print_summary(self):
        """Print text summary to console."""
        summary = self.engine.get_financial_summary()

        print("\n" + "=" * 60)
        print("SIMULATION SUMMARY")
        print("=" * 60)

        print(f"\n{'FINANCIALS':-^40}")
        print(f"  Total Revenue:      {self._fmt_money(summary['total_revenue']):>12}")
        print(f"  Fixed Prod Cost:    {self._fmt_money(summary['total_fixed_production_cost']):>12}")
        print(f"  Variable Prod Cost: {self._fmt_money(summary['total_variable_production_cost']):>12}")
        print(f"  Truck Shipping:     {self._fmt_money(summary['total_truck_shipping_cost']):>12}")
        print(f"  Mail Shipping:      {self._fmt_money(summary['total_mail_shipping_cost']):>12}")
        print(f"  Fulfilment Cost:   {self._fmt_money(summary['total_fulfilment_cost']):>12}")
        print(f"  Holding Cost:       {self._fmt_money(summary['total_holding_cost']):>12}")
        print(f"  CapEx:              {self._fmt_money(summary['total_capex']):>12}")
        print(f"  Interest Earned:    {self._fmt_money(summary['total_interest']):>12}")
        print(f"  {'─' * 38}")
        print(f"  Total Profit:       {self._fmt_money(summary['total_profit']):>12}")
        print(f"  Final Cash:         {self._fmt_money(summary['cash']):>12}")

        # Factory shutdown timing
        print(f"\n{'FACTORY SHUTDOWN':-^40}")
        factories = ["Calopeia_Factory", "Sorange_Factory", "Fardo_Factory"]
        first_production_day = {f: None for f in factories}
        last_production_day = {f: None for f in factories}
        first_shutdown_day = {f: None for f in factories}  # First day factory stops after producing

        # Scan daily records to find production timing per factory
        for record in self.engine.daily_records:
            for factory, warehouse, qty, method in record.production_started:
                if first_production_day[factory] is None:
                    first_production_day[factory] = record.day
                last_production_day[factory] = record.day

        # Find first shutdown day: first day where factory was idle after having produced
        # (capacity > 0, WIP = 0, no production started, and had produced before)
        for record in self.engine.daily_records:
            for factory in factories:
                # Skip if we already found first shutdown
                if first_shutdown_day[factory] is not None:
                    continue
                # Skip if factory never produced
                if first_production_day[factory] is None:
                    continue
                # Skip if this is before first production
                if record.day <= first_production_day[factory]:
                    continue

                capacity = record.factory_capacities.get(factory, 0)
                wip = record.factory_wip.get(factory, 0)
                produced_today = any(f == factory for f, w, q, m in record.production_started)

                # Factory is idle: has capacity, no WIP, didn't start production
                if capacity > 0 and wip == 0 and not produced_today:
                    first_shutdown_day[factory] = record.day

        for factory in factories:
            fname = factory.replace('_Factory', '')
            first_day = first_production_day[factory]
            last_day = last_production_day[factory]
            shutdown_day = first_shutdown_day[factory]

            if first_day is None:
                print(f"  {fname:12} Never produced")
            else:
                shutdown_str = f"first idle: {shutdown_day}" if shutdown_day else "never idle"
                print(f"  {fname:12} Produced: {first_day}-{last_day} ({shutdown_str})")

        # Leftover inventory
        print(f"\n{'LEFTOVER INVENTORY':-^40}")
        COST_PER_DRUM = 985  # FC/200 + VC + Transport/200 = $10 + $900 + $75
        warehouses = ["Calopeia_WH", "Sorange_WH", "Tyran_WH", "Fardo_WH"]
        leftover = {wh: self.engine.warehouses[wh].inventory if wh in self.engine.warehouses else 0 for wh in warehouses}
        total_leftover = sum(leftover.values())

        inv_str = " | ".join(f"{wh.replace('_WH', '')[:3]}:{leftover[wh]}" for wh in warehouses)
        print(f"  {inv_str} | Total: {total_leftover}")
        print(f"  Tied-up capital: {self._fmt_money(total_leftover * COST_PER_DRUM)}")

        print("\n" + "FULFILMENT".center(40, "-"))
        d = self.data
        total_demand = sum(sum(d.regional_demand[r]) for r in d.regional_demand)
        total_fulfilled = total_demand - summary['total_stockouts']
        fill_rate = (total_fulfilled / total_demand * 100) if total_demand > 0 else 0

        print(f"  Total Demand:       {total_demand:>12,} drums")
        print(f"  Total Fulfilled:    {total_fulfilled:>12,} drums")
        print(f"  Total Stockouts:    {summary['total_stockouts']:>12,} drums")
        print(f"  Fill Rate:          {fill_rate:>11.2f}%")
        print(f"  Opportunity Cost:   {self._fmt_money(summary['total_stockouts'] * STOCKOUT_COST_PER_DRUM):>12}")

        print("\n  By Region:")
        for region in ["Calopeia", "Sorange", "Tyran", "Entworpe", "Fardo"]:
            region_demand = sum(d.regional_demand[region]) if d.regional_demand[region] else 0
            region_stockout = d.regional_cum_stockout[region][-1] if d.regional_cum_stockout[region] else 0
            region_fulfilled = region_demand - region_stockout
            region_fill = (region_fulfilled / region_demand * 100) if region_demand > 0 else 0
            print(f"    {region:12} {region_stockout:>6,} lost | {region_fill:>5.1f}% fill")

        print("\n" + "=" * 60)
