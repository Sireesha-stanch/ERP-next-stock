"""Exception hierarchy for the stock ledger."""

from __future__ import annotations

from fractions import Fraction


class StockError(Exception):
    """Base class for all errors raised by this package."""


class InvalidVoucherError(StockError):
    """A voucher or voucher line violates a structural rule."""


class UnknownVoucherError(StockError):
    """The referenced voucher (or line) does not exist in the ledger."""


class InsufficientQuantityError(StockError):
    """A FIFO queue was asked to give out more quantity than it holds."""

    def __init__(self, shortfall: Fraction):
        super().__init__(f"short by {shortfall}")
        self.shortfall = shortfall


class NegativeStockError(StockError):
    """Reposting would drive a stream below zero (ERPNext: negative stock not allowed).

    Raised *before* anything is committed, so the ledger is left untouched.
    """

    def __init__(self, item_code: str, warehouse: str, voucher_no: str, shortfall: Fraction):
        super().__init__(
            f"{voucher_no}: {item_code} @ {warehouse} would go negative "
            f"(short by {shortfall})"
        )
        self.item_code = item_code
        self.warehouse = warehouse
        self.voucher_no = voucher_no
        self.shortfall = shortfall
