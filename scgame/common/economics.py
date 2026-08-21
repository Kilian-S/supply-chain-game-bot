"""Authoritative game economics for both runs.

Every figure here is taken from the CIVE70108 assignment notes. Where the
assignment notes and the introductory slides disagree, the notes take
precedence, because they state explicitly that their values override the ones
shown on the scenario description pages. The one disagreement that matters is
the customer fulfilment cost, which the slides give as $160 per drum and the
notes give as $150 per drum. The $150 figure is used throughout.

Holding these constants in one module is what keeps the two simulators, the two
decision engines, and the written analysis quoting the same numbers.
"""

# --- Timing -----------------------------------------------------------------

GAME_START_DAY = 730          # First day under the bot's control
GAME_END_DAY = 1460           # Last day of the simulated horizon
ENDGAME_START_DAY = 1430      # Demand begins its linear decline to zero here
SEASONAL_PERIOD = 365

CAPACITY_BUILD_DAYS = 90      # Delay before ordered capacity comes online
WAREHOUSE_BUILD_DAYS = 60     # Delay before an ordered warehouse opens

# --- Revenue ----------------------------------------------------------------

REVENUE_PER_DRUM = 1450.0

# --- Production -------------------------------------------------------------

FIXED_COST_PER_BATCH = 2000.0
VARIABLE_COST_PER_DRUM = 900.0

# --- Inventory --------------------------------------------------------------

HOLDING_COST_PER_DRUM_PER_YEAR = 90.0
HOLDING_COST_PER_DRUM_PER_DAY = HOLDING_COST_PER_DRUM_PER_YEAR / 365.0  # $0.2466

# --- Shipping, factory to warehouse -----------------------------------------

TRUCK_CAPACITY = 200

TRUCK_COST_SAME_REGION = 15_000.0
TRUCK_COST_CROSS_REGION = 20_000.0
TRUCK_COST_TO_FARDO = 45_000.0

MAIL_COST_SAME_REGION = 150.0
MAIL_COST_CROSS_REGION = 200.0
MAIL_COST_TO_FARDO = 400.0

SHIPPING_DAYS_TRUCK = 7
SHIPPING_DAYS_TRUCK_TO_FARDO = 14
SHIPPING_DAYS_MAIL = 1
SHIPPING_DAYS_MAIL_TO_FARDO = 2

# --- Customer fulfilment, warehouse to customer -----------------------------
# Charged per drum sold, and priced by the distance from the fulfilling
# warehouse to the customer's region. The rates match the mail rates above.

FULFILMENT_COST_SAME_REGION = 150.0
FULFILMENT_COST_CROSS_REGION = 200.0
FULFILMENT_COST_TO_FARDO = 400.0

# --- Capital expenditure ----------------------------------------------------

FACTORY_BASE_COST = 500_000.0
FACTORY_COST_PER_CAPACITY_UNIT = 50_000.0
WAREHOUSE_BUILD_COST = 100_000.0

# --- Finance ----------------------------------------------------------------

ANNUAL_INTEREST_RATE = 0.10
DAILY_INTEREST_RATE = (1 + ANNUAL_INTEREST_RATE) ** (1 / 365) - 1


def contribution_per_drum(
    batch_size: int = TRUCK_CAPACITY,
    truck_cost: float = TRUCK_COST_SAME_REGION,
    fulfilment_cost: float = FULFILMENT_COST_SAME_REGION,
) -> float:
    """Return the profit earned on one drum sold, net of every variable cost.

    A batch of `batch_size` drums carries one fixed order charge and fills
    `batch_size / TRUCK_CAPACITY` trucks, so both are amortised across the batch.
    For a full 200-drum truck delivered and fulfilled within one region this
    evaluates to $315 per drum, the figure the inventory policy is built on:

        1450 - 2000/200 - 900 - 15000/200 - 150 = 315

    A drum can therefore be held for 315 / 0.2466 = 1277 days before its
    accumulated holding cost exceeds the profit lost by not having it in stock.
    That asymmetry is the reason both runs deliberately carry surplus inventory.
    """
    trucks_per_batch = -(-batch_size // TRUCK_CAPACITY)  # Ceiling division
    return (
        REVENUE_PER_DRUM
        - FIXED_COST_PER_BATCH / batch_size
        - VARIABLE_COST_PER_DRUM
        - trucks_per_batch * truck_cost / batch_size
        - fulfilment_cost
    )


def holding_days_to_break_even(contribution: float = None) -> float:
    """Return how long a drum may be held before holding cost erases its profit."""
    if contribution is None:
        contribution = contribution_per_drum()
    return contribution / HOLDING_COST_PER_DRUM_PER_DAY


# The opportunity cost of failing to serve one unit of demand. An unfilled order
# is lost to a competitor after one day rather than backordered, so the loss is
# exactly the contribution that drum would have earned.
STOCKOUT_COST_PER_DRUM = contribution_per_drum()
