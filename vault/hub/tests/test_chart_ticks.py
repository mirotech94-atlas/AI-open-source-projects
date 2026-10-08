"""Gridline values a person would actually write.

The axis was cut into equal thirds and the raw value printed, so a security
score chart topping out at 100 carried labels reading 33.3333 and 66.6667.
Nobody writes an axis that way, and it appeared in a report meant to be read by
someone's client.
"""

import pytest

from guardian_hub.reports.sections import _ticks


@pytest.mark.parametrize(
    "top, expected",
    [
        (100, [0.0, 25.0, 50.0, 75.0, 100.0]),   # the score chart he saw
        (1.0, [0.0, 0.25, 0.5, 0.75, 1.0]),
        (12, [0.0, 5.0, 10.0]),
    ],
)
def test_ticks_land_on_round_numbers(top, expected):
    assert _ticks(top) == expected


def test_no_tick_has_a_repeating_decimal():
    for top in (100, 63, 4700, 0.35, 7, 240):
        for v in _ticks(top):
            rendered = f"{v:g}"
            assert len(rendered.split(".")[-1]) <= 3 if "." in rendered else True, (
                f"top={top} produced the label {rendered!r}"
            )
            assert "3333" not in rendered and "6667" not in rendered


def test_ticks_start_at_zero_and_never_exceed_the_top():
    for top in (100, 63, 4700, 0.35, 7):
        ticks = _ticks(top)
        assert ticks[0] == 0.0
        assert ticks[-1] <= top + 1e-9


def test_a_flat_or_empty_axis_does_not_divide_by_zero():
    assert _ticks(0) == [0.0]
    assert _ticks(-5) == [0.0]
