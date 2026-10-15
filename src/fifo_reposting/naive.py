"""Full-rebuild valuation: the simple, obviously-correct oracle.

It replays the *entire* ledger from the beginning and ignores every stored balance.
It is deliberately written independently of :mod:`reposter` (no scheduling, no early
stop, no staged state) so the test-suite can use it to check the incremental engine,
and so operators can use it as a "rebuild and compare" integrity job.

Cost is O(rows in the whole ledger) - exactly what incremental reposting avoids.
"""

from __future__ import annotations

from collections import defaultdict
from fractions import Fraction
from typing import Dict

from .fifo import FifoQueue
from .ledger import StockLedger
from .models import Computed, RateMode, Stream


def full_rebuild(ledger: StockLedger) -> Dict[int, Computed]:
    """Return the correct ``Computed`` for every row, computed from scratch."""
    queues: Dict[Stream, FifoQueue] = defaultdict(FifoQueue)
    consumed: Dict[str, Fraction] = defaultdict(Fraction)
    produced: Dict[str, Fraction] = defaultdict(Fraction)
    for row in ledger.rows_in_order():
        if row.qty > 0 and row.rate_mode is RateMode.FROM_CONSUMPTION:
            produced[row.voucher_no] += row.qty

    result: Dict[int, Computed] = {}
    for row in ledger.rows_in_order():  # issues sort before receipts inside a voucher
        queue = queues[row.stream]
        if row.qty < 0:
            queue, value = queue.consume(-row.qty)
            consumed[row.voucher_no] += value
            computed = Computed(value / -row.qty, -value, queue.qty, queue.value, queue)
        else:
            if row.rate_mode is RateMode.GIVEN:
                rate = row.rate
            else:
                rate = consumed[row.voucher_no] / produced[row.voucher_no]
            queue = queue.add(row.qty, rate)
            computed = Computed(rate, row.qty * rate, queue.qty, queue.value, queue)
        queues[row.stream] = queue
        result[row.id] = computed
    return result
