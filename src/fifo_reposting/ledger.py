"""In-memory stock ledger: storage, ordering and lookups. No valuation logic here."""

from __future__ import annotations

from bisect import bisect_left, insort
from contextlib import contextmanager
from fractions import Fraction
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from .errors import InvalidVoucherError, UnknownVoucherError
from .models import Computed, SortKey, StockLedgerEntry, Stream, Voucher


class StockLedger:
    """Rows of all streams, kept in a total order per stream.

    Mutations can be wrapped in :meth:`transaction`; if the block raises, every
    mutation made inside it is undone, so a failed repost never leaves half-written
    state behind.
    """

    def __init__(self) -> None:
        self._rows: Dict[int, StockLedgerEntry] = {}
        self._streams: Dict[Stream, List[Tuple[SortKey, int]]] = {}
        self._voucher_rows: Dict[str, List[int]] = {}
        self._next_id = 1
        self._next_creation = 1
        self._undo: Optional[List[Callable[[], None]]] = None

    # ------------------------------------------------------------------ reads
    def __len__(self) -> int:
        return len(self._rows)

    def get(self, entry_id: int) -> StockLedgerEntry:
        return self._rows[entry_id]

    def rows_in_order(self) -> List[StockLedgerEntry]:
        """All rows in global posting order."""
        return sorted(self._rows.values(), key=lambda row: row.key)

    def streams(self) -> List[Stream]:
        return sorted(s for s, index in self._streams.items() if index)

    def stream_rows(self, stream: Stream) -> List[StockLedgerEntry]:
        return [self._rows[entry_id] for _, entry_id in self._streams.get(stream, [])]

    def has_voucher(self, voucher_no: str) -> bool:
        return voucher_no in self._voucher_rows

    def voucher_rows(self, voucher_no: str) -> List[StockLedgerEntry]:
        try:
            return [self._rows[i] for i in self._voucher_rows[voucher_no]]
        except KeyError:
            raise UnknownVoucherError(f"unknown voucher {voucher_no!r}") from None

    def first_at_or_after(self, stream: Stream, key: SortKey) -> Optional[StockLedgerEntry]:
        index = self._streams.get(stream, [])
        position = bisect_left(index, (key, 0))
        return self._rows[index[position][1]] if position < len(index) else None

    def entry_at(self, stream: Stream, key: SortKey) -> StockLedgerEntry:
        row = self.first_at_or_after(stream, key)
        if row is None or row.key != key:
            raise KeyError((stream, key))
        return row

    def previous(self, row: StockLedgerEntry) -> Optional[StockLedgerEntry]:
        index = self._streams[row.stream]
        position = bisect_left(index, (row.key, row.id))
        return self._rows[index[position - 1][1]] if position > 0 else None

    def following(self, row: StockLedgerEntry) -> Optional[StockLedgerEntry]:
        index = self._streams[row.stream]
        position = bisect_left(index, (row.key, row.id)) + 1
        return self._rows[index[position][1]] if position < len(index) else None

    def snapshot(self) -> Dict[int, Optional[Computed]]:
        """``{row id: computed}`` - handy for equality checks in tests."""
        return {row_id: row.computed for row_id, row in self._rows.items()}

    # --------------------------------------------------------------- mutations
    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Undo every mutation made inside the block if it raises."""
        if self._undo is not None:
            raise RuntimeError("transactions cannot be nested")
        self._undo = []
        try:
            yield
        except BaseException:
            for undo in reversed(self._undo):
                undo()
            raise
        finally:
            self._undo = None

    def _log_undo(self, undo: Callable[[], None]) -> None:
        if self._undo is not None:
            self._undo.append(undo)

    def _insert(self, row: StockLedgerEntry) -> None:
        self._rows[row.id] = row
        insort(self._streams.setdefault(row.stream, []), (row.key, row.id))

    def _delete(self, row: StockLedgerEntry) -> None:
        del self._rows[row.id]
        self._streams[row.stream].remove((row.key, row.id))

    def add_voucher(self, voucher: Voucher) -> List[StockLedgerEntry]:
        """Insert the rows of ``voucher`` (unvalued) and return them."""
        if voucher.voucher_no in self._voucher_rows:
            raise InvalidVoucherError(f"voucher {voucher.voucher_no!r} already exists")
        saved_counters = (self._next_id, self._next_creation)
        creation = self._next_creation
        self._next_creation += 1
        rows: List[StockLedgerEntry] = []
        for line_no, line in enumerate(voucher.ordered_lines()):
            row = StockLedgerEntry(
                id=self._next_id,
                voucher_type=voucher.voucher_type,
                voucher_no=voucher.voucher_no,
                line_no=line_no,
                item_code=line.item_code,
                warehouse=line.warehouse,
                posting_dt=voucher.posting_dt,
                creation=creation,
                qty=line.qty,
                rate=line.rate,
                rate_mode=line.rate_mode,
            )
            self._next_id += 1
            self._insert(row)
            rows.append(row)
        self._voucher_rows[voucher.voucher_no] = [row.id for row in rows]

        def undo() -> None:
            for row in rows:
                self._delete(row)
            del self._voucher_rows[voucher.voucher_no]
            self._next_id, self._next_creation = saved_counters

        self._log_undo(undo)
        return rows

    def remove_voucher(self, voucher_no: str) -> List[StockLedgerEntry]:
        """Delete all rows of a voucher (cancellation) and return them."""
        rows = self.voucher_rows(voucher_no)
        for row in rows:
            self._delete(row)
        del self._voucher_rows[voucher_no]

        def undo() -> None:
            for row in rows:
                self._insert(row)
            self._voucher_rows[voucher_no] = [row.id for row in rows]

        self._log_undo(undo)
        return rows

    def set_inputs(
        self,
        entry_id: int,
        *,
        qty: Optional[Fraction] = None,
        rate: Optional[Fraction] = None,
    ) -> StockLedgerEntry:
        """Change the user-editable inputs of a row (not its computed values)."""
        row = self._rows[entry_id]
        old_qty, old_rate = row.qty, row.rate
        if qty is not None:
            row.qty = qty
        if rate is not None:
            row.rate = rate

        def undo() -> None:
            row.qty, row.rate = old_qty, old_rate

        self._log_undo(undo)
        return row

    def commit(self, changes: Dict[int, Computed]) -> None:
        """Apply computed values for many rows at once (the last step of a repost)."""
        previous = {entry_id: self._rows[entry_id].computed for entry_id in changes}
        for entry_id, computed in changes.items():
            self._rows[entry_id].computed = computed

        def undo() -> None:
            for entry_id, computed in previous.items():
                if entry_id in self._rows:
                    self._rows[entry_id].computed = computed

        self._log_undo(undo)

    # ------------------------------------------------------------- consistency
    def verify_running_balances(self) -> List[str]:
        """Check the cheap invariants of the stored running balance.

        Returns a list of human-readable problems (empty list = consistent). This is
        the safety net for the failure modes of persisting a running balance on every
        row: partially applied reposts, drift, manual edits.
        """
        problems: List[str] = []
        for stream in self.streams():
            previous: Optional[Computed] = None
            for row in self.stream_rows(stream):
                c = row.computed
                where = f"{row.voucher_no} ({stream[0]} @ {stream[1]})"
                if c is None:
                    problems.append(f"{where}: not valued")
                    previous = None
                    continue
                base_qty = previous.qty_after_transaction if previous else Fraction(0)
                base_value = previous.stock_value if previous else Fraction(0)
                if c.qty_after_transaction != c.queue.qty:
                    problems.append(f"{where}: qty_after_transaction disagrees with the FIFO queue")
                if c.stock_value != c.queue.value:
                    problems.append(f"{where}: stock_value disagrees with the FIFO queue")
                if c.qty_after_transaction != base_qty + row.qty:
                    problems.append(f"{where}: balance qty does not follow from the previous row")
                if c.stock_value != base_value + c.stock_value_difference:
                    problems.append(f"{where}: balance value does not follow from the previous row")
                previous = c
        return problems
