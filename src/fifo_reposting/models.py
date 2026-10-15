"""Data model: vouchers (inputs), ledger entries (rows) and computed valuation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from fractions import Fraction
from typing import Optional, Tuple

from .errors import InvalidVoucherError
from .fifo import FifoQueue

#: A stream is one independent FIFO queue: ``(item_code, warehouse)``.
Stream = Tuple[str, str]

#: Total order of ledger rows inside a stream (and globally):
#: ``(posting_dt, creation, line_no)``. ``creation`` breaks ties between vouchers
#: posted at the same instant, ``line_no`` between lines of one voucher.
SortKey = Tuple[datetime, int, int]


class RateMode(str, Enum):
    """Where the rate of an *inbound* line comes from."""

    #: The user typed it (Purchase Receipt, Opening Stock, ...).
    GIVEN = "given"
    #: It is derived from what the same voucher consumed (Manufacture finished
    #: goods, the receiving side of a Material Transfer, ...).
    FROM_CONSUMPTION = "from_consumption"


@dataclass(frozen=True)
class LineSpec:
    """One line of a voucher. ``qty`` is signed: ``> 0`` receipt, ``< 0`` issue.

    Issues never carry a rate - FIFO decides it. Receipts carry a rate unless it is
    derived from the voucher's consumption.
    """

    item_code: str
    warehouse: str
    qty: Fraction
    rate: Optional[Fraction] = None
    rate_mode: RateMode = RateMode.GIVEN

    def __post_init__(self) -> None:
        if self.qty == 0:
            raise InvalidVoucherError("line quantity must not be zero")
        if self.qty < 0:
            if self.rate is not None or self.rate_mode is not RateMode.GIVEN:
                raise InvalidVoucherError("an issue line cannot carry a rate; FIFO values it")
        elif self.rate_mode is RateMode.GIVEN:
            if self.rate is None or self.rate < 0:
                raise InvalidVoucherError("a receipt line needs a non-negative rate")
        elif self.rate is not None:
            raise InvalidVoucherError("a derived-rate receipt line must not carry a rate")

    @property
    def is_inbound(self) -> bool:
        return self.qty > 0


@dataclass(frozen=True)
class Voucher:
    voucher_type: str
    voucher_no: str
    posting_dt: datetime
    lines: Tuple[LineSpec, ...]

    def __post_init__(self) -> None:
        if not self.lines:
            raise InvalidVoucherError("a voucher needs at least one line")
        derived = any(l.rate_mode is RateMode.FROM_CONSUMPTION for l in self.lines)
        if derived and not any(l.qty < 0 for l in self.lines):
            raise InvalidVoucherError("derived-rate lines need at least one issue line to derive from")

    def ordered_lines(self) -> Tuple[LineSpec, ...]:
        """Issues first, then receipts (stable).

        The reposter relies on this: by the time a derived-rate receipt is valued,
        every issue of the same voucher has already been valued.
        """
        outbound = [l for l in self.lines if l.qty < 0]
        inbound = [l for l in self.lines if l.qty > 0]
        return tuple(outbound + inbound)


@dataclass(frozen=True)
class Computed:
    """Everything the valuation engine derives for one row.

    ``qty_after_transaction``, ``stock_value`` and ``queue`` are the *running balance*
    (what ERPNext persists on every Stock Ledger Entry). ``valuation_rate`` and
    ``stock_value_difference`` describe the movement itself.
    """

    valuation_rate: Fraction
    stock_value_difference: Fraction
    qty_after_transaction: Fraction
    stock_value: Fraction
    queue: FifoQueue

    def same_movement(self, other: "Computed") -> bool:
        return (
            self.valuation_rate == other.valuation_rate
            and self.stock_value_difference == other.stock_value_difference
        )


@dataclass
class StockLedgerEntry:
    """A row of the stock ledger (ERPNext: Stock Ledger Entry)."""

    id: int
    voucher_type: str
    voucher_no: str
    line_no: int
    item_code: str
    warehouse: str
    posting_dt: datetime
    creation: int
    qty: Fraction
    rate: Optional[Fraction]
    rate_mode: RateMode
    computed: Optional[Computed] = None

    @property
    def stream(self) -> Stream:
        return (self.item_code, self.warehouse)

    @property
    def key(self) -> SortKey:
        return (self.posting_dt, self.creation, self.line_no)
