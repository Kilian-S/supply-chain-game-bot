"""Parsing helpers in the shared browser layer.

These run without a browser. The navigation itself cannot be tested offline,
because the game server closed at the end of the assessed run.
"""

import pytest

from scgame.common.browser_controller import (
    BrowserGameController,
    GameUnavailableError,
)

parse = BrowserGameController.parse_capacity


def test_capacity_is_read_from_an_operational_factory():
    """The usual case, where the page reports a capacity to two decimals."""
    assert parse("Factory is operational with a current capacity of 70.02.") == 70


def test_capacity_is_read_when_the_page_omits_the_decimal_part():
    """A whole number must parse too.

    An earlier expression required a decimal point, so a page reading
    "current capacity of 50" raised an attribute error and killed the cycle.
    """
    assert parse("Factory is operational with a current capacity of 50.") == 50


def test_capacity_is_truncated_rather_than_rounded():
    """A factory cannot finish a partial drum, so the fraction is discarded."""
    assert parse("current capacity of 49.98") == 49


def test_a_factory_still_being_built_reports_no_usable_capacity():
    """Scheduled capacity produces nothing until it comes online."""
    assert parse("Total scheduled capacity is 85.04.") == 0


def test_an_operational_reading_wins_over_a_scheduled_one():
    """An expanding factory shows both figures, and only the live one counts."""
    page = (
        "Factory is operational with a current capacity of 70.00. "
        "Total scheduled capacity is 100.00."
    )
    assert parse(page) == 70


def test_a_page_with_no_capacity_figure_fails_loudly():
    """Silently returning zero would look like a factory that had stopped."""
    with pytest.raises(GameUnavailableError):
        parse("The server is temporarily unavailable.")
