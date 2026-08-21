"""The cost model must agree with the figures published in the coursework."""

import pytest

from scgame.common import economics


def test_contribution_matches_the_reported_same_region_figure():
    """A full truck delivered and sold within one region earns $315 per drum."""
    assert economics.contribution_per_drum() == pytest.approx(315.0)


def test_contribution_matches_the_reported_cross_region_shipping_figure():
    """Shipping to a warehouse in another continental region costs $25 more."""
    contribution = economics.contribution_per_drum(
        truck_cost=economics.TRUCK_COST_CROSS_REGION,
        fulfilment_cost=economics.FULFILMENT_COST_SAME_REGION,
    )
    assert contribution == pytest.approx(290.0)


def test_contribution_matches_the_reported_cross_region_fulfilment_figure():
    """Serving a customer in another region costs $50 more per drum."""
    contribution = economics.contribution_per_drum(
        truck_cost=economics.TRUCK_COST_SAME_REGION,
        fulfilment_cost=economics.FULFILMENT_COST_CROSS_REGION,
    )
    assert contribution == pytest.approx(265.0)


def test_serving_the_island_from_the_mainland_destroys_most_of_the_margin():
    """Fulfilling Fardo from a mainland warehouse leaves $65 per drum.

    This is the calculation that justified building a factory and a warehouse on
    the island rather than supplying it across the water.
    """
    contribution = economics.contribution_per_drum(
        truck_cost=economics.TRUCK_COST_SAME_REGION,
        fulfilment_cost=economics.FULFILMENT_COST_TO_FARDO,
    )
    assert contribution == pytest.approx(65.0)


def test_larger_batches_earn_more_per_drum():
    """Spreading the fixed order charge over more drums raises the margin."""
    small = economics.contribution_per_drum(200)
    medium = economics.contribution_per_drum(400)
    large = economics.contribution_per_drum(600)
    assert small < medium < large


def test_holding_is_far_cheaper_than_stocking_out():
    """A drum can be held for over three years before it stops paying its way.

    This asymmetry is the entire justification for carrying surplus inventory,
    so it is worth asserting rather than assuming.
    """
    assert economics.holding_days_to_break_even() > 1200


def test_stockout_cost_equals_the_contribution_that_was_missed():
    """An unfilled order is lost outright, so the loss is the whole margin."""
    assert economics.STOCKOUT_COST_PER_DRUM == pytest.approx(
        economics.contribution_per_drum()
    )
