"""Convenience constructors for the voucher types used in the task and tests."""

from __future__ import annotations

from datetime import datetime
from typing import Sequence, Tuple

from .models import LineSpec, RateMode, Voucher
from .numbers import Numeric, to_number

ItemQty = Tuple[str, str, Numeric]  # (item_code, warehouse, qty)


def opening_stock(voucher_no: str, at: datetime, item: str, warehouse: str,
                  qty: Numeric, rate: Numeric) -> Voucher:
    return Voucher("Opening Stock", voucher_no, at,
                   (LineSpec(item, warehouse, to_number(qty), to_number(rate)),))


def purchase_receipt(voucher_no: str, at: datetime, item: str, warehouse: str,
                     qty: Numeric, rate: Numeric) -> Voucher:
    return Voucher("Purchase Receipt", voucher_no, at,
                   (LineSpec(item, warehouse, to_number(qty), to_number(rate)),))


def delivery_note(voucher_no: str, at: datetime, item: str, warehouse: str,
                  qty: Numeric) -> Voucher:
    return Voucher("Delivery Note", voucher_no, at,
                   (LineSpec(item, warehouse, -to_number(qty)),))


def manufacture(voucher_no: str, at: datetime,
                consume: Sequence[ItemQty], produce: Sequence[ItemQty]) -> Voucher:
    """Stock Entry (Manufacture): finished goods are valued at the cost of what was consumed."""
    lines = [LineSpec(i, w, -to_number(q)) for i, w, q in consume]
    lines += [LineSpec(i, w, to_number(q), None, RateMode.FROM_CONSUMPTION) for i, w, q in produce]
    return Voucher("Stock Entry (Manufacture)", voucher_no, at, tuple(lines))


def material_transfer(voucher_no: str, at: datetime, item: str,
                      from_warehouse: str, to_warehouse: str, qty: Numeric) -> Voucher:
    """Stock Entry (Material Transfer): the receiving side carries the cost that left the source."""
    qty = to_number(qty)
    return Voucher("Stock Entry (Material Transfer)", voucher_no, at, (
        LineSpec(item, from_warehouse, -qty),
        LineSpec(item, to_warehouse, qty, None, RateMode.FROM_CONSUMPTION),
    ))
