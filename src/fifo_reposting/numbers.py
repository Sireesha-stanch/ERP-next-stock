"""Exact-number helpers.

All quantities, rates and values are ``fractions.Fraction``. Exact arithmetic is a
deliberate choice: the reposter decides when it may stop by *comparing* recomputed
state with stored state, and that comparison is only trustworthy if the same inputs
always give bit-identical outputs. Binary floats cannot promise that. Rounding is a
presentation concern (see :func:`format_number`).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Union

Numeric = Union[int, str, Decimal, Fraction]


def to_number(value: Numeric) -> Fraction:
    """Convert ``int``/``str``/``Decimal``/``Fraction`` to an exact ``Fraction``.

    Floats are rejected on purpose: ``Fraction(0.1)`` is not one tenth.
    """
    if isinstance(value, Fraction):
        return value
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError(f"use int, str or Decimal instead of {type(value).__name__}: {value!r}")
    if isinstance(value, (int, str, Decimal)):
        return Fraction(value)
    raise TypeError(f"cannot convert {type(value).__name__} to a number")


def format_number(value: Fraction, places: int = 2) -> str:
    """Render a ``Fraction`` rounded half-up to ``places`` decimals."""
    quantum = Decimal(1).scaleb(-places)
    decimal = Decimal(value.numerator) / Decimal(value.denominator)
    return str(decimal.quantize(quantum, rounding=ROUND_HALF_UP))
