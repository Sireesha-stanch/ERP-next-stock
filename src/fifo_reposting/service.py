"""Public entry points. Every operation is atomic: it fully applies or leaves no trace."""

from __future__ import annotations

from typing import Optional

from .errors import InvalidVoucherError, UnknownVoucherError
from .ledger import StockLedger
from .models import RateMode, StockLedgerEntry, Voucher
from .numbers import Numeric, to_number
from .reposter import RepostResult, Reposter, Seed


class StockService:
    """Post, amend and cancel vouchers; valuation is reposted incrementally each time.

    Posting a brand-new voucher is just a repost seeded at the new rows - there is no
    separate "append" path, so backdated entries need no special casing.
    """

    def __init__(self, ledger: Optional[StockLedger] = None) -> None:
        self.ledger = ledger if ledger is not None else StockLedger()

    def post_voucher(self, voucher: Voucher) -> RepostResult:
        with self.ledger.transaction():
            rows = self.ledger.add_voucher(voucher)
            return Reposter(self.ledger).repost((row.stream, row.key) for row in rows)

    def edit_incoming_rate(self, voucher_no: str, new_rate: Numeric, *,
                           line_no: Optional[int] = None,
                           item_code: Optional[str] = None) -> RepostResult:
        """Change the rate of a user-rated receipt line (the scenario in the task)."""
        rate = to_number(new_rate)
        if rate < 0:
            raise InvalidVoucherError("rate must not be negative")
        row = self._resolve_row(voucher_no, line_no, item_code,
                                lambda r: r.qty > 0 and r.rate_mode is RateMode.GIVEN)
        return self._edit(row, rate=rate)

    def edit_quantity(self, voucher_no: str, new_qty: Numeric, *,
                      line_no: Optional[int] = None,
                      item_code: Optional[str] = None) -> RepostResult:
        """Change the quantity of a line, keeping its direction (receipt stays receipt)."""
        qty = to_number(new_qty)
        row = self._resolve_row(voucher_no, line_no, item_code, lambda r: True)
        if qty == 0 or (qty > 0) != (row.qty > 0):
            raise InvalidVoucherError("new quantity must be non-zero and keep the original sign")
        return self._edit(row, qty=qty)

    def cancel_voucher(self, voucher_no: str) -> RepostResult:
        with self.ledger.transaction():
            removed = self.ledger.remove_voucher(voucher_no)
            seeds = [(row.stream, row.key) for row in removed]
            return Reposter(self.ledger).repost(seeds)

    # ------------------------------------------------------------------ helpers
    def _edit(self, row: StockLedgerEntry, *, qty=None, rate=None) -> RepostResult:
        voucher_no = row.voucher_no
        with self.ledger.transaction():
            self.ledger.set_inputs(row.id, qty=qty, rate=rate)
            # Seed every line of the voucher: a voucher is valued as a unit (a derived
            # receipt reads the voucher's issues), and seeding them all up front keeps
            # the global replay order correct. Rows whose result does not change stop
            # the replay immediately, so this costs next to nothing.
            seeds: list[Seed] = [(r.stream, r.key) for r in self.ledger.voucher_rows(voucher_no)]
            return Reposter(self.ledger).repost(seeds)

    def _resolve_row(self, voucher_no, line_no, item_code, predicate) -> StockLedgerEntry:
        rows = [r for r in self.ledger.voucher_rows(voucher_no) if predicate(r)]
        if line_no is not None:
            rows = [r for r in rows if r.line_no == line_no]
        if item_code is not None:
            rows = [r for r in rows if r.item_code == item_code]
        if not rows:
            raise UnknownVoucherError(f"no matching editable line in {voucher_no!r}")
        if len(rows) > 1:
            raise InvalidVoucherError(
                f"{voucher_no!r} has several matching lines; pass line_no or item_code"
            )
        return rows[0]
