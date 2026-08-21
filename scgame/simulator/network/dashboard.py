# Rich-based terminal dashboard for the simulator.
# Modelled after visualizer_reference.py with sparklines, bot log, and keyboard controls.
# Supports both single-region (legacy) and multi-region (network) modes.

import sys
import logging
import threading
import time
from collections import deque
from typing import Optional, Callable, List, Dict

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box


from .engine import NetworkEngine, DailyRecord


# Display constants
CHART_HISTORY_DAYS = 60
MAX_LOG_LINES = 6

# Speed tiers (days per second)
SPEED_TIERS = [1, 2, 5, 10, 20, 50]


class BotLogHandler(logging.Handler):
    """Custom logging handler that captures messages for the bot log panel."""

    def __init__(self, max_messages: int = 50):
        super().__init__()
        self.messages: deque = deque(maxlen=max_messages)

    def emit(self, record):
        try:
            msg = self.format(record)
            self.messages.append(msg)
        except Exception:
            pass


# === Multi-Region Network Visualizer ===

class NetworkVisualizer:
    """
    Multi-region dashboard for the NetworkEngine.

    Features:
    - Per-system panels showing factories, warehouses, routes
    - Regional inventory and stockout tracking
    - Sparkline charts per warehouse
    - Cross-warehouse fulfilment visualization
    - Keyboard controls: pause, step, speed +/-, quit
    """

    def __init__(
        self,
        engine: NetworkEngine,
        speed: float = 5.0,
    ):
        self.engine = engine
        self.speed = speed
        self._speed_index = self._nearest_speed_index(speed)
        self.speed = SPEED_TIERS[self._speed_index]
        self.console = Console()

        # State
        self.paused = False
        self.step_requested = False
        self.quit_requested = False
        self.current_record: Optional[DailyRecord] = None

        # Per-warehouse history
        self.warehouse_histories: Dict[str, deque] = {
            wh: deque(maxlen=CHART_HISTORY_DAYS)
            for wh in ["Calopeia_WH", "Sorange_WH", "Tyran_WH", "Fardo_WH"]
        }
        self.demand_histories: Dict[str, deque] = {
            region: deque(maxlen=CHART_HISTORY_DAYS)
            for region in ["Calopeia", "Sorange", "Tyran", "Entworpe", "Fardo"]
        }

        # Bot log handler
        self.log_handler = BotLogHandler(max_messages=MAX_LOG_LINES * 3)
        self.log_handler.setFormatter(logging.Formatter('%(message)s'))

        # Keyboard listener
        self._input_thread = None
        self._stop_input_thread = False

    def _nearest_speed_index(self, speed: float) -> int:
        diffs = [abs(s - speed) for s in SPEED_TIERS]
        return diffs.index(min(diffs))

    def get_log_handler(self) -> BotLogHandler:
        return self.log_handler

    # === Keyboard handling ===

    def _start_keyboard_listener(self):
        self._stop_input_thread = False
        self._input_thread = threading.Thread(target=self._keyboard_loop, daemon=True)
        self._input_thread.start()

    def _stop_keyboard_listener(self):
        self._stop_input_thread = True
        if self._input_thread:
            self._input_thread.join(timeout=0.5)

    def _keyboard_loop(self):
        try:
            if sys.platform == 'win32':
                import msvcrt
                while not self._stop_input_thread:
                    if msvcrt.kbhit():
                        key = msvcrt.getch().decode('utf-8', errors='ignore')
                        self._handle_key(key)
                    time.sleep(0.05)
            else:
                import select
                import tty
                import termios

                old_settings = termios.tcgetattr(sys.stdin)
                try:
                    tty.setcbreak(sys.stdin.fileno())
                    while not self._stop_input_thread:
                        if select.select([sys.stdin], [], [], 0.05)[0]:
                            key = sys.stdin.read(1)
                            self._handle_key(key)
                finally:
                    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        except Exception:
            pass

    def _handle_key(self, key: str):
        if key == ' ':
            self.paused = not self.paused
        elif key.lower() == 's' and self.paused:
            self.step_requested = True
        elif key.lower() == 'q':
            self.quit_requested = True
        elif key == '=' or key == '+':
            if self._speed_index < len(SPEED_TIERS) - 1:
                self._speed_index += 1
                self.speed = SPEED_TIERS[self._speed_index]
        elif key == '-':
            if self._speed_index > 0:
                self._speed_index -= 1
                self.speed = SPEED_TIERS[self._speed_index]

    # === Utilities ===

    def _sparkline(self, values: List[float], color: str = "white", width: int = 30) -> str:
        if not values:
            return ""
        chars = "▁▂▃▄▅▆▇█"
        min_val = min(values)
        max_val = max(values)
        range_val = max_val - min_val if max_val > min_val else 1

        if len(values) > width:
            step = len(values) / width
            sampled = [values[int(i * step)] for i in range(width)]
        else:
            sampled = values

        line = ""
        for v in sampled:
            normalised = (v - min_val) / range_val
            char_idx = min(int(normalised * (len(chars) - 1)), len(chars) - 1)
            line += chars[char_idx]

        return f"[{color}]{line}[/{color}]"

    def _fmt_money(self, val: float) -> str:
        if abs(val) >= 1_000_000:
            return f"${val/1_000_000:.2f}M"
        elif abs(val) >= 1_000:
            return f"${val/1_000:.1f}K"
        else:
            return f"${val:.0f}"

    # === Panel builders ===

    def _build_header(self) -> Panel:
        start_day = 730
        end_day = 1460
        current_day = self.engine.current_day
        total_days = end_day - start_day
        days_done = current_day - start_day

        progress = days_done / total_days if total_days > 0 else 0
        bar_width = 25
        filled = int(progress * bar_width)
        bar = "[green]" + "█" * filled + "[/green]" + "░" * (bar_width - filled)

        if self.paused:
            status = "[bold red]PAUSED[/bold red]"
        elif self.quit_requested:
            status = "[bold red]STOPPING[/bold red]"
        else:
            status = "[bold green]RUNNING[/bold green]"

        cash_str = self._fmt_money(self.engine.cash)
        cash_color = "green" if self.engine.cash >= 0 else "red"

        stockouts = self.engine.total_stockouts

        header_text = (
            f"[bold]Day {current_day}[/bold]/{end_day}  {bar}  "
            f"[bold]Cash:[/bold] [{cash_color}]{cash_str}[/{cash_color}]  "
            f"[bold]Stockouts:[/bold] [red]{stockouts}[/red]  "
            f"{status}  "
            f"[bold]Speed:[/bold] {self.speed}x"
        )

        return Panel(Text.from_markup(header_text), box=box.SIMPLE)

    def _build_system_panel(self, system_name: str, factory: str, warehouses: List[str], regions: List[str]) -> Panel:
        """Build a panel for one system (factory + warehouses + regions)."""
        # Get system mode
        mode = self.engine.get_system_mode(system_name)
        mode_colors = {"BUILD": "green", "CHASE": "blue", "DRAWDOWN": "red"}
        mode_color = mode_colors.get(mode, "dim")
        mode_display = f"[{mode_color}]{mode or 'N/A'}[/{mode_color}]"

        table = Table(show_header=True, box=box.SIMPLE, padding=(0, 1))
        table.add_column("Component", style="bold")
        table.add_column("Inv", justify="right")
        table.add_column("Transit", justify="right")
        table.add_column("ROP", justify="right")
        table.add_column("Qty", justify="right")
        table.add_column("Ship", justify="right")

        # Factory row
        factory_cap = self.engine.get_factory_capacity(factory)
        factory_wip = self.engine.factories[factory].wip_total if factory in self.engine.factories else 0

        if factory_cap == 0:
            table.add_row(f"[dim]{factory.replace('_Factory', '')}[/dim]", "-", "-", "-", "-", "[dim]Not built[/dim]")
        else:
            table.add_row(f"[yellow]{factory.replace('_Factory', '')} Fac[/yellow]", f"WIP:{factory_wip}", f"Cap:{factory_cap}", "-", "-", "-")

        # Warehouse rows with settings
        for wh in warehouses:
            wh_short = wh.replace('_WH', '')
            if self.engine.is_warehouse_online(wh):
                wh_state = self.engine.warehouses[wh]
                inv = wh_state.inventory
                transit = wh_state.in_transit_total

                # Get route settings for this warehouse
                route_settings = self.engine.factories[factory].route_settings.get(wh)
                if route_settings:
                    rop = route_settings.reorder_point
                    qty = route_settings.order_quantity
                    ship = route_settings.shipping_method
                    ship_color = "green" if ship == "TRUCK" else "yellow"
                    ship_display = f"[{ship_color}]{ship[0]}[/{ship_color}]"  # T or M
                else:
                    rop, qty, ship_display = "-", "-", "-"

                # Colour based on inventory level
                inv_color = "green" if inv > 200 else ("yellow" if inv > 50 else "red")
                table.add_row(f"[cyan]{wh_short} WH[/cyan]", f"[{inv_color}]{inv}[/{inv_color}]", f"[blue]{transit}[/blue]", f"{rop}", f"{qty}", ship_display)
            else:
                table.add_row(f"[dim]{wh_short} WH[/dim]", "-", "-", "-", "-", "[dim]Not built[/dim]")

        # Today's demand/fulfilment
        if self.current_record:
            table.add_row("", "", "", "", "", "")
            for region in regions:
                demand = self.current_record.regional_demand.get(region, 0)
                result = self.current_record.fulfilment_results.get(region)
                if result:
                    fulfilled = result.fulfilled
                    stockout = result.stockout
                    if stockout > 0:
                        table.add_row(f"  {region}", f"[red]↓{stockout}[/red]", f"{fulfilled}/{demand}", "", "", "")
                    else:
                        table.add_row(f"  {region}", "[green]✓[/green]", f"{fulfilled}/{demand}", "", "", "")

        system_color = {"calopeia": "cyan", "sorange": "green", "fardo": "yellow"}.get(system_name.lower(), "white")
        title = f"[bold {system_color}]{system_name.upper()}[/bold {system_color}] {mode_display}"
        return Panel(table, title=title, border_style=system_color)

    def _build_charts_panel(self) -> Panel:
        """Build sparkline charts for all warehouses."""
        lines = []

        for wh, color in [("Calopeia_WH", "cyan"), ("Sorange_WH", "green"), ("Tyran_WH", "blue"), ("Fardo_WH", "yellow")]:
            history = list(self.warehouse_histories.get(wh, []))
            if len(history) >= 2:
                sparkline = self._sparkline(history, color=color)
                avg = sum(history) / len(history)
                lines.append(f"[bold]{wh[:7]}[/bold] {sparkline} avg:{avg:.0f}")
            else:
                lines.append(f"[bold]{wh[:7]}[/bold] [dim]collecting...[/dim]")

        return Panel(
            Text.from_markup("\n".join(lines)),
            title="[bold]INVENTORY TRENDS[/bold]",
            border_style="dim"
        )

    def _build_financials_panel(self) -> Panel:
        """Build financial summary panel."""
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("Metric", style="bold")
        table.add_column("Value", justify="right")

        table.add_row("Revenue", f"[green]{self._fmt_money(self.engine.total_revenue)}[/green]")
        table.add_row("Fixed Prod", f"[red]{self._fmt_money(self.engine.total_fixed_production_cost)}[/red]")
        table.add_row("Var Prod", f"[red]{self._fmt_money(self.engine.total_variable_production_cost)}[/red]")
        table.add_row("Truck Ship", f"[red]{self._fmt_money(self.engine.total_truck_shipping_cost)}[/red]")
        table.add_row("Mail Ship", f"[red]{self._fmt_money(self.engine.total_mail_shipping_cost)}[/red]")
        table.add_row("Fulfilment", f"[red]{self._fmt_money(self.engine.total_fulfilment_cost)}[/red]")
        table.add_row("Holding", f"[red]{self._fmt_money(self.engine.total_holding_cost)}[/red]")
        table.add_row("CapEx", f"[red]{self._fmt_money(self.engine.total_capex)}[/red]")
        table.add_row("Interest", f"[green]{self._fmt_money(self.engine.total_interest)}[/green]")
        table.add_row("─" * 10, "─" * 8)
        profit = self.engine.total_profit
        profit_color = "green" if profit >= 0 else "red"
        table.add_row("[bold]Profit[/bold]", f"[bold {profit_color}]{self._fmt_money(profit)}[/bold {profit_color}]")

        return Panel(table, title="[bold]FINANCIALS[/bold]", border_style="magenta")

    def _build_log_panel(self) -> Panel:
        """Bot log panel."""
        recent_logs = list(self.log_handler.messages)[-MAX_LOG_LINES:]

        if not recent_logs:
            log_text = "[dim]No actions yet...[/dim]"
        else:
            lines = []
            for msg in recent_logs:
                if "STOCKOUT" in msg.upper() or "LOST" in msg.upper():
                    lines.append(f"[red]{msg}[/red]")
                elif "order" in msg.lower() or "produce" in msg.lower():
                    lines.append(f"[yellow]{msg}[/yellow]")
                elif "fulfilled" in msg.lower():
                    lines.append(f"[green]{msg}[/green]")
                else:
                    lines.append(f"[dim]{msg}[/dim]")
            log_text = "\n".join(lines)

        return Panel(Text.from_markup(log_text), title="[bold]LOG[/bold]", border_style="magenta")

    def _build_footer(self) -> Panel:
        pause_text = "[bold red]PAUSED[/bold red]  " if self.paused else ""
        controls = (
            f"{pause_text}"
            "[bold][SPACE][/bold] Pause  "
            "[bold][+/-][/bold] Speed  "
            "[bold][S][/bold] Step  "
            "[bold][Q][/bold] Quit"
        )
        return Panel(Text.from_markup(controls), box=box.SIMPLE)

    def _build_layout(self) -> Panel:
        """Build the complete multi-region dashboard."""
        layout = Layout()

        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="footer", size=3)
        )

        # Body: systems on left, charts/financials on right
        layout["body"].split_row(
            Layout(name="systems", ratio=2),
            Layout(name="sidebar", ratio=1)
        )

        # Systems column: 3 systems stacked
        layout["systems"].split_column(
            Layout(name="calopeia"),
            Layout(name="sorange"),
            Layout(name="fardo")
        )

        # Sidebar: charts, financials, log
        layout["sidebar"].split_column(
            Layout(name="charts"),
            Layout(name="financials"),
            Layout(name="log")
        )

        # Fill sections
        layout["header"].update(self._build_header())

        layout["calopeia"].update(self._build_system_panel(
            "Calopeia", "Calopeia_Factory",
            ["Calopeia_WH", "Tyran_WH"],
            ["Calopeia", "Tyran", "Entworpe"]
        ))
        layout["sorange"].update(self._build_system_panel(
            "Sorange", "Sorange_Factory",
            ["Sorange_WH"],
            ["Sorange"]
        ))
        layout["fardo"].update(self._build_system_panel(
            "Fardo", "Fardo_Factory",
            ["Fardo_WH"],
            ["Fardo"]
        ))

        layout["charts"].update(self._build_charts_panel())
        layout["financials"].update(self._build_financials_panel())
        layout["log"].update(self._build_log_panel())
        layout["footer"].update(self._build_footer())

        return Panel(
            layout,
            title="[bold blue]MULTI-REGION SUPPLY CHAIN SIMULATOR[/bold blue]",
            border_style="blue",
            box=box.ROUNDED
        )

    def update_history(self, record: DailyRecord):
        """Update chart history with new record."""
        for wh in self.warehouse_histories:
            if wh in record.warehouse_inventories:
                total = record.warehouse_inventories[wh] + record.warehouse_in_transit.get(wh, 0)
                self.warehouse_histories[wh].append(total)

        for region in self.demand_histories:
            if region in record.regional_demand:
                self.demand_histories[region].append(record.regional_demand[region])

    def run(
        self,
        bot_cycle_callback: Callable[[], None] = None,
        on_complete: Callable[[], None] = None,
    ):
        """
        Run the visualization loop.

        Args:
            bot_cycle_callback: Function to call each day for bot decisions.
            on_complete: Function to call when simulation completes.
        """
        self._start_keyboard_listener()

        try:
            with Live(
                self._build_layout(),
                console=self.console,
                refresh_per_second=15,
                screen=True,
            ) as live:
                while not self.engine.is_game_over and not self.quit_requested:
                    if self.paused and not self.step_requested:
                        live.update(self._build_layout())
                        time.sleep(0.1)
                        continue

                    self.step_requested = False

                    # Run bot decisions
                    if bot_cycle_callback:
                        try:
                            bot_cycle_callback()
                        except Exception:
                            pass

                    # Step simulation
                    self.current_record = self.engine.step()
                    self.update_history(self.current_record)

                    # Update display
                    live.update(self._build_layout())

                    # Delay based on speed
                    if not self.paused and self.speed > 0:
                        time.sleep(1.0 / self.speed)

                # Final update
                live.update(self._build_layout())
                time.sleep(0.5)

        finally:
            self._stop_keyboard_listener()

        if on_complete:
            on_complete()
