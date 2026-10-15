"""The scenario from the task, as code.

Milk and Milk Powder, both FIFO. Dates are in 2026 (the task only gives day/month).
"""

from __future__ import annotations

from datetime import datetime
from typing import Tuple

from .numbers import Numeric
from .reposter import RepostResult
from .service import StockService
from .vouchers import delivery_note, manufacture, opening_stock, purchase_receipt

MILK = "Milk"
MILK_POWDER = "Milk Powder"
WAREHOUSE = "Stores"

EDITED_VOUCHER = "PR-0001"  # the 10 January Purchase Receipt


def _at(month: int, day: int) -> datetime:
    return datetime(2026, month, day, 10, 0)


def build_task_ledger(first_receipt_rate: Numeric = 60) -> StockService:
    """Post every transaction of the task, in date order."""
    service = StockService()
    service.post_voucher(opening_stock("OPEN-0001", _at(1, 1), MILK, WAREHOUSE, 100, 50))
    service.post_voucher(purchase_receipt(EDITED_VOUCHER, _at(1, 10), MILK, WAREHOUSE, 50, first_receipt_rate))
    service.post_voucher(delivery_note("DN-0001", _at(1, 15), MILK, WAREHOUSE, 80))
    service.post_voucher(purchase_receipt("PR-0002", _at(1, 20), MILK, WAREHOUSE, 50, 60))
    service.post_voucher(manufacture(
        "STE-0001", _at(1, 21),
        consume=[(MILK, WAREHOUSE, 100)],
        produce=[(MILK_POWDER, WAREHOUSE, 10)],
    ))
    service.post_voucher(delivery_note("DN-0002", _at(1, 25), MILK_POWDER, WAREHOUSE, 10))
    return service


def run_task_scenario(new_rate: Numeric = 80) -> Tuple[StockService, RepostResult]:
    """Build the ledger, then edit the 10 Jan receipt from 60 to ``new_rate`` and repost."""
    service = build_task_ledger()
    result = service.edit_incoming_rate(EDITED_VOUCHER, new_rate)
    return service, result
