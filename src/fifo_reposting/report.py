"""Plain-text rendering of ledgers and repost results (used by the demo and docs)."""

from __future__ import annotations

from fractions import Fraction
from typing import List, Optional, Sequence

from .ledger import StockLedger
from .numbers import format_number
from .reposter import ChangeKind, RepostResult

_HEADER = ("Date", "Voucher", "Item", "Qty", "Rate", "Value chg", "Bal qty", "Bal value", "FIFO queue after")


def _qty(value: Fraction) -> str:
    text = format_number(value, 0 if value.denominator == 1 else 2)
    return text if value < 0 else f"+{text}"


def render_ledger(ledger: StockLedger, item_code: Optional[str] = None) -> str:
    """Render the ledger (optionally one item) in posting order."""
    rows: List[Sequence[str]] = []
    for row in ledger.rows_in_order():
        if item_code and row.item_code != item_code:
            continue
        date = row.posting_dt.strftime("%d %b")
        c = row.computed
        if c is None:
            rows.append((date, row.voucher_no, row.item_code, _qty(row.qty),
                         "-", "-", "-", "-", "(not valued)"))
            continue
        queue = ", ".join(
            f"{format_number(layer.qty, 0 if layer.qty.denominator == 1 else 2)}@{format_number(layer.rate)}"
            for layer in c.queue.layers
        ) or "-"
        rows.append((
            date, row.voucher_no, row.item_code, _qty(row.qty),
            format_number(c.valuation_rate), format_number(c.stock_value_difference),
            format_number(c.qty_after_transaction), format_number(c.stock_value), queue,
        ))
    return _table(_HEADER, rows)


def render_result(result: RepostResult) -> str:
    """Summarise what a repost touched, grouped by how it changed."""
    lines = [
        f"rows visited: {result.rows_visited} of {result.rows_in_ledger} in the ledger"
        f"  |  rows rewritten: {len(result.changes)}",
    ]
    sections = (
        (ChangeKind.REVALUED, "REVALUED   - the movement's rate/value changed (GL must be reposted)"),
        (ChangeKind.REBALANCED, "REBALANCED - only the running balance changed (movement intact)"),
        (ChangeKind.NEW, "NEW"),
    )
    for kind, title in sections:
        changes = result.of_kind(kind)
        if not changes:
            continue
        lines.append(f"\n{title}")
        for c in changes:
            detail = ""
            if kind is ChangeKind.REVALUED and c.before is not None:
                detail = (
                    f"rate {format_number(c.before.valuation_rate)} -> {format_number(c.after.valuation_rate)}, "
                    f"value {format_number(c.before.stock_value_difference)} -> "
                    f"{format_number(c.after.stock_value_difference)}"
                )
            elif kind is ChangeKind.REBALANCED and c.before is not None:
                detail = (
                    f"balance value {format_number(c.before.stock_value)} -> "
                    f"{format_number(c.after.stock_value)}"
                )
            lines.append(f"  {c.key[0]:%d %b}  {c.voucher_no:<9} {c.item_code:<12} {detail}")
    return "\n".join(lines)


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = [max(len(cell) for cell in column) for column in zip(header, *rows)]

    def fmt(cells: Sequence[str]) -> str:
        return "  ".join(cell.ljust(width) for cell, width in zip(cells, widths)).rstrip()

    return "\n".join([fmt(header), fmt(["-" * w for w in widths]), *(fmt(r) for r in rows)])
