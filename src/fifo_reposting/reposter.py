"""Incremental FIFO reposting.

Problem
-------
A past transaction changed (edited rate/qty, backdated insert, cancellation). Every row
whose valuation depended on it must be recomputed - but *only* those.

Key observation
---------------
The valuation of a row is a pure function of

    (FIFO queue after the previous row of the same item+warehouse, the row's own inputs)

plus, for derived-rate receipts (manufacture output, transfer-in), the value consumed
by the other lines of the same voucher. So:

* We replay a stream forward from the first affected row, starting from the *stored*
  queue snapshot of the row before it.
* If a recomputed row leaves the queue exactly as it was stored, nothing after it can
  change (unless some later row has changed inputs of its own) -> **stop early**.
* If an issue's *movement* changed (different value taken out), the receipts derived
  from it, possibly in another item's stream, become dirty -> **propagate** there.
* Streams are replayed together in global posting order (a heap), which guarantees a
  row is always valued after everything it depends on.
* Nothing is written while replaying. All results are staged and committed in one go,
  so a failure (e.g. negative stock) leaves the ledger exactly as it was.
"""

from __future__ import annotations

import heapq
from bisect import bisect_right, insort
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .errors import InsufficientQuantityError, NegativeStockError
from .fifo import FifoQueue
from .ledger import StockLedger
from .models import Computed, RateMode, SortKey, StockLedgerEntry, Stream

Seed = Tuple[Stream, SortKey]
"""``(stream, key)``: "re-value the first row at or after ``key`` in ``stream``"."""


class ChangeKind(str, Enum):
    NEW = "new"
    #: Rate / value of the movement itself changed -> GL entries must be reposted.
    REVALUED = "revalued"
    #: Only the running balance (stock value, queue) changed; the movement is intact.
    REBALANCED = "rebalanced"


@dataclass(frozen=True)
class RowChange:
    entry_id: int
    voucher_type: str
    voucher_no: str
    item_code: str
    warehouse: str
    key: SortKey
    kind: ChangeKind
    before: Optional[Computed]
    after: Computed

    @property
    def value_delta(self) -> Fraction:
        """Change in the value moved by this row (what a GL repost must book)."""
        before = self.before.stock_value_difference if self.before else Fraction(0)
        return self.after.stock_value_difference - before


@dataclass(frozen=True)
class RepostResult:
    changes: Tuple[RowChange, ...]
    rows_visited: int
    rows_in_ledger: int

    def of_kind(self, kind: ChangeKind) -> List[RowChange]:
        return [c for c in self.changes if c.kind is kind]

    def voucher_numbers(self, kind: ChangeKind) -> List[str]:
        """Distinct voucher numbers with at least one change of ``kind``, in posting order."""
        seen: List[str] = []
        for change in self.of_kind(kind):
            if change.voucher_no not in seen:
                seen.append(change.voucher_no)
        return seen


class Reposter:
    """Recomputes valuation for everything affected by ``seeds`` and commits it atomically."""

    def __init__(self, ledger: StockLedger) -> None:
        self.ledger = ledger

    def repost(self, seeds: Iterable[Seed]) -> RepostResult:
        return _Run(self.ledger).execute(seeds)


class _Run:
    """State of a single repost. Created fresh per call, so Reposter itself is stateless."""

    def __init__(self, ledger: StockLedger) -> None:
        self.ledger = ledger
        self.staged: Dict[int, Computed] = {}
        self.originals: Dict[int, Optional[Computed]] = {}
        self.cursor: Dict[Stream, SortKey] = {}
        self.forced: Dict[Stream, List[SortKey]] = {}
        self.heap: List[Tuple[SortKey, Stream]] = []
        self.visited_ids: Set[int] = set()

    # ------------------------------------------------------------------ driver
    def execute(self, seeds: Iterable[Seed]) -> RepostResult:
        for stream, key in seeds:
            # Seeds are pinned: a seeded row's own inputs changed (edited, new, or the row
            # before it was cancelled), so the stream may not stop before reaching it even
            # if an earlier seeded row of the same stream turns out to be stable.
            self._schedule(stream, key, forced=True)
        while self.heap:
            key, stream = heapq.heappop(self.heap)
            if self.cursor.get(stream) != key:
                continue  # stale heap entry: the stream was re-scheduled earlier
            del self.cursor[stream]
            self._visit(self.ledger.entry_at(stream, key))
        self.ledger.commit(self.staged)
        return self._result()

    def _schedule(self, stream: Stream, key: SortKey, *, forced: bool = False) -> None:
        """Make sure the first row at/after ``key`` in ``stream`` will be visited.

        ``forced`` additionally pins that row: the stream must not stop early before
        reaching it, because its *own inputs* changed (not merely the queue before it).
        """
        row = self.ledger.first_at_or_after(stream, key)
        if row is None:
            return
        if forced:
            pinned = self.forced.setdefault(stream, [])
            if row.key not in pinned:
                insort(pinned, row.key)
        current = self.cursor.get(stream)
        if current is None or row.key < current:
            self.cursor[stream] = row.key
            heapq.heappush(self.heap, (row.key, stream))

    # ------------------------------------------------------------------- visit
    def _visit(self, row: StockLedgerEntry) -> None:
        stream = row.stream
        self.visited_ids.add(row.id)
        pinned = self.forced.get(stream)
        if pinned and row.key in pinned:
            pinned.remove(row.key)

        old = self._current(row)
        new = self._compute(row, self._queue_before(row))

        state_changed = old is None or old.queue != new.queue
        movement_changed = old is None or not old.same_movement(new)
        if old != new:
            self.originals.setdefault(row.id, old)
            self.staged[row.id] = new

        # An issue that moved a different value changes the cost of whatever the same
        # voucher derives from it (manufactured goods, transfer-in) - maybe in another stream.
        if movement_changed and row.qty < 0:
            for sibling in self.ledger.voucher_rows(row.voucher_no):
                if sibling.rate_mode is RateMode.FROM_CONSUMPTION:
                    self._schedule(sibling.stream, sibling.key, forced=True)

        following = self.ledger.following(row)
        if following is None:
            return
        if state_changed:
            # Everything after this row started from a different queue: keep replaying.
            self._schedule(stream, following.key)
            return
        # The queue is back to what was stored. Later rows are unaffected, except rows
        # that were pinned because their own inputs changed: jump straight to the next one.
        pinned = self.forced.get(stream, [])
        position = bisect_right(pinned, row.key)
        if position < len(pinned):
            self._schedule(stream, pinned[position])

    # --------------------------------------------------------------- valuation
    def _current(self, row: StockLedgerEntry) -> Optional[Computed]:
        """Latest known value of a row: staged by this run, else what is stored."""
        return self.staged.get(row.id, row.computed)

    def _queue_before(self, row: StockLedgerEntry) -> FifoQueue:
        previous = self.ledger.previous(row)
        if previous is None:
            return FifoQueue()
        computed = self._current(previous)
        if computed is None:  # pragma: no cover - would indicate a scheduling bug
            raise RuntimeError(f"internal error: {previous.voucher_no} is not valued yet")
        return computed.queue

    def _compute(self, row: StockLedgerEntry, queue_before: FifoQueue) -> Computed:
        if row.qty > 0:
            rate = row.rate if row.rate_mode is RateMode.GIVEN else self._derived_rate(row)
            assert rate is not None
            queue = queue_before.add(row.qty, rate)
            return Computed(rate, row.qty * rate, queue.qty, queue.value, queue)

        try:
            queue, value = queue_before.consume(-row.qty)
        except InsufficientQuantityError as error:
            raise NegativeStockError(
                row.item_code, row.warehouse, row.voucher_no, error.shortfall
            ) from None
        return Computed(value / -row.qty, -value, queue.qty, queue.value, queue)

    def _derived_rate(self, row: StockLedgerEntry) -> Fraction:
        """Cost per unit of a derived-rate receipt: value consumed / units produced."""
        consumed = Fraction(0)
        produced = Fraction(0)
        for sibling in self.ledger.voucher_rows(row.voucher_no):
            if sibling.qty < 0:
                computed = self._current(sibling)
                if computed is None:  # pragma: no cover - would indicate a scheduling bug
                    raise RuntimeError(f"internal error: {sibling.voucher_no} issue not valued yet")
                consumed -= computed.stock_value_difference
            elif sibling.rate_mode is RateMode.FROM_CONSUMPTION:
                produced += sibling.qty
        return consumed / produced

    # ------------------------------------------------------------------ result
    def _result(self) -> RepostResult:
        changes: List[RowChange] = []
        for entry_id, after in self.staged.items():
            row = self.ledger.get(entry_id)
            before = self.originals[entry_id]
            if before is None:
                kind = ChangeKind.NEW
            elif before.same_movement(after):
                kind = ChangeKind.REBALANCED
            else:
                kind = ChangeKind.REVALUED
            changes.append(
                RowChange(
                    entry_id, row.voucher_type, row.voucher_no, row.item_code,
                    row.warehouse, row.key, kind, before, after,
                )
            )
        changes.sort(key=lambda change: change.key)
        return RepostResult(tuple(changes), len(self.visited_ids), len(self.ledger))
