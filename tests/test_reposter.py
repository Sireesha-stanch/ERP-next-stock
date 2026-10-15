"""Behaviour of the reposter beyond the task scenario."""

from fractions import Fraction as F

import pytest

from fifo_reposting import (
    ChangeKind, InvalidVoucherError, NegativeStockError, StockService, UnknownVoucherError,
    full_rebuild,
)
from fifo_reposting.vouchers import (
    delivery_note, manufacture, material_transfer, opening_stock, purchase_receipt,
)

from conftest import at

WH = "Stores"


def assert_consistent(service):
    assert service.ledger.verify_running_balances() == []
    assert service.ledger.snapshot() == full_rebuild(service.ledger)


# ------------------------------------------------------------------ early stop
def absorbed_ledger():
    s = StockService()
    s.post_voucher(purchase_receipt("PR-A", at(1, 1), "X", WH, 10, 5))
    s.post_voucher(purchase_receipt("PR-B", at(1, 2), "X", WH, 10, 6))
    s.post_voucher(delivery_note("DN-1", at(1, 3), "X", WH, 10))   # consumes all of PR-A's layer
    s.post_voucher(purchase_receipt("PR-C", at(1, 4), "X", WH, 10, 7))
    s.post_voucher(delivery_note("DN-2", at(1, 5), "X", WH, 10))
    s.post_voucher(delivery_note("DN-3", at(1, 6), "X", WH, 5))
    return s


def test_replay_stops_once_the_queue_is_back_to_what_it_was():
    s = absorbed_ledger()
    untouched = {r.voucher_no: r.computed for r in s.ledger.rows_in_order()}

    result = s.edit_incoming_rate("PR-A", 9)

    # After DN-1 the queue is [10@6] with or without the edit: the change is absorbed there.
    assert [c.voucher_no for c in result.changes] == ["PR-A", "PR-B", "DN-1"]
    assert result.rows_visited == 3 and result.rows_in_ledger == 6
    assert result.voucher_numbers(ChangeKind.REVALUED) == ["PR-A", "DN-1"]
    assert result.voucher_numbers(ChangeKind.REBALANCED) == ["PR-B"]
    for later in ("PR-C", "DN-2", "DN-3"):
        assert s.ledger.voucher_rows(later)[0].computed is untouched[later]  # not even rewritten
    assert_consistent(s)


def test_noop_edit_writes_nothing():
    s = absorbed_ledger()
    result = s.edit_incoming_rate("PR-A", 5)
    assert result.changes == ()
    assert result.rows_visited == 1


# --------------------------------------------------------- backdate / cancel
def test_backdated_receipt_revalues_later_issues():
    s = StockService()
    s.post_voucher(purchase_receipt("PR-1", at(1, 10), "X", WH, 10, 10))
    s.post_voucher(delivery_note("DN-1", at(1, 20), "X", WH, 8))
    assert s.ledger.voucher_rows("DN-1")[0].computed.stock_value_difference == -80

    # A cheaper receipt discovered later, dated *before* the first one: it is now the oldest layer.
    result = s.post_voucher(purchase_receipt("PR-0", at(1, 5), "X", WH, 4, 1))
    assert s.ledger.voucher_rows("DN-1")[0].computed.stock_value_difference == -(4 * 1 + 4 * 10)
    assert "DN-1" in result.voucher_numbers(ChangeKind.REVALUED)
    assert_consistent(s)


def test_same_timestamp_vouchers_are_ordered_by_creation():
    s = StockService()
    s.post_voucher(purchase_receipt("PR-1", at(1, 1), "X", WH, 5, 10))
    s.post_voucher(purchase_receipt("PR-2", at(1, 1), "X", WH, 5, 20))
    s.post_voucher(delivery_note("DN-1", at(1, 1), "X", WH, 5))
    assert s.ledger.voucher_rows("DN-1")[0].computed.stock_value_difference == -50  # oldest first
    assert_consistent(s)


def test_cancel_receipt_revalues_what_came_after(task_service):
    s = task_service
    s.edit_incoming_rate("PR-0002", 70)  # keep the manufacture feasible, then cancel a different way
    s.cancel_voucher("DN-0001")          # the 15 Jan delivery never happened
    # Now 20 more litres of the 50-rate layer are still there on 21 Jan.
    assert s.ledger.voucher_rows("STE-0001")[0].computed.stock_value_difference == -(
        100 * 50)
    assert_consistent(s)


def test_cancel_that_would_cause_negative_stock_is_rejected_and_rolled_back(task_service):
    s = task_service
    before = dict(s.ledger.snapshot())
    rows_before = len(s.ledger)
    with pytest.raises(NegativeStockError) as info:
        s.cancel_voucher("PR-0002")  # only 70 L left for the 100 L manufacture
    assert info.value.item_code == "Milk"
    assert info.value.voucher_no == "STE-0001"
    assert info.value.shortfall == 30
    assert s.ledger.has_voucher("PR-0002")
    assert len(s.ledger) == rows_before
    assert s.ledger.snapshot() == before


def test_failed_post_leaves_no_trace(task_service):
    s = task_service
    before = dict(s.ledger.snapshot())
    with pytest.raises(NegativeStockError):
        s.post_voucher(delivery_note("DN-9", at(1, 22), "Milk", WH, 999))
    assert not s.ledger.has_voucher("DN-9")
    assert s.ledger.snapshot() == before
    # and the service is still fully usable afterwards
    s.post_voucher(delivery_note("DN-9", at(1, 22), "Milk", WH, 5))
    assert_consistent(s)


# ------------------------------------------------------- cross-item cascades
def test_cost_cascades_through_two_manufacturing_stages():
    s = StockService()
    s.post_voucher(purchase_receipt("PR-1", at(1, 1), "Milk", WH, 100, 50))
    s.post_voucher(manufacture("MF-1", at(1, 2), [("Milk", WH, 100)], [("Powder", WH, 10)]))
    s.post_voucher(manufacture("MF-2", at(1, 3), [("Powder", WH, 10)], [("Choc", WH, 20)]))
    s.post_voucher(delivery_note("DN-1", at(1, 4), "Choc", WH, 5))

    result = s.edit_incoming_rate("PR-1", 60)

    assert s.ledger.voucher_rows("DN-1")[0].computed.stock_value_difference == -(6000 / 20 * 5)
    assert result.voucher_numbers(ChangeKind.REVALUED) == ["PR-1", "MF-1", "MF-2", "DN-1"]
    assert_consistent(s)


def test_material_transfer_carries_cost_to_the_other_warehouse():
    s = StockService()
    s.post_voucher(opening_stock("OP", at(1, 1), "X", "A", 10, 5))
    s.post_voucher(purchase_receipt("PR", at(1, 2), "X", "A", 10, 7))
    s.post_voucher(material_transfer("MT", at(1, 3), "X", "A", "B", 15))
    s.post_voucher(delivery_note("DN", at(1, 4), "X", "B", 12))
    # 15 units leave A at a cost of 10*5 + 5*7 = 85, and arrive in B as ONE layer at the
    # blended rate 85/15 (the receiving side of a transfer is valued at the source's
    # outgoing rate, as in ERPNext) - so 12 units delivered from B cost 12 * 85/15 = 68.
    assert s.ledger.voucher_rows("DN")[0].computed.stock_value_difference == -68

    s.edit_incoming_rate("PR", 9)  # cost leaving A is now 10*5 + 5*9 = 95 -> 12 * 95/15 = 76
    assert s.ledger.voucher_rows("DN")[0].computed.stock_value_difference == -76
    assert_consistent(s)


def test_co_products_share_the_consumed_cost_per_unit():
    s = StockService()
    s.post_voucher(purchase_receipt("PR", at(1, 1), "Raw", WH, 10, 10))
    s.post_voucher(manufacture("MF", at(1, 2), [("Raw", WH, 10)],
                               [("A", WH, 3), ("B", WH, 2)]))
    rate_a = s.ledger.voucher_rows("MF")[1].computed.valuation_rate
    rate_b = s.ledger.voucher_rows("MF")[2].computed.valuation_rate
    assert rate_a == rate_b == 20  # 100 / 5 units

    s.edit_quantity("MF", 3, item_code="B")  # 100 / 6 units - exact, no rounding
    assert s.ledger.voucher_rows("MF")[1].computed.valuation_rate == F(100, 6)
    assert_consistent(s)


def test_stream_that_stops_early_still_reaches_a_later_row_whose_inputs_changed():
    """A repack consumes Q and P and re-produces P.

    Editing the repack's Q quantity seeds all its lines. The P *issue* line is stable
    (its queue is unchanged), so replaying stream P would stop right there - but the P
    *receipt* line of the same voucher is derived from the changed Q cost and must still
    be revalued. This is what the "pinned rows" bookkeeping in the reposter is for.
    """
    s = StockService()
    s.post_voucher(purchase_receipt("PR-P", at(1, 1), "P", WH, 10, 5))
    s.post_voucher(purchase_receipt("PR-Q", at(1, 1, 11), "Q", WH, 10, 2))
    s.post_voucher(manufacture("REPACK", at(1, 3), [("Q", WH, 10), ("P", WH, 4)], [("P", WH, 6)]))
    assert s.ledger.voucher_rows("REPACK")[2].computed.valuation_rate == F(20 + 20, 6)

    s.edit_quantity("REPACK", -5, item_code="Q", line_no=0)  # Q consumed: 10 -> 5 (issues are negative)

    assert s.ledger.voucher_rows("REPACK")[2].computed.valuation_rate == F(10 + 20, 6)
    assert_consistent(s)


def test_regression_edited_receipt_after_a_stable_issue_in_the_same_stream():
    """Found by the randomized test: both lines of a repack are seeded, the earlier one
    (the P issue) is stable, and the later one (the P receipt, whose own quantity was
    edited) must still be revalued rather than skipped."""
    s = StockService()
    s.post_voucher(purchase_receipt("PR-P", at(1, 1), "P", WH, 10, 5))
    s.post_voucher(purchase_receipt("PR-Q", at(1, 1, 11), "Q", WH, 10, 2))
    s.post_voucher(manufacture("REPACK", at(1, 3), [("Q", WH, 10), ("P", WH, 4)], [("P", WH, 6)]))

    s.edit_quantity("REPACK", 8, item_code="P", line_no=2)  # receipt line: 6 -> 8 units

    receipt = s.ledger.voucher_rows("REPACK")[2].computed
    assert receipt.valuation_rate == F(40, 8)  # 40 of cost now spread over 8 units
    assert receipt.stock_value_difference == 40
    assert_consistent(s)


def test_edit_quantity_of_a_receipt_changes_later_issues():
    s = StockService()
    s.post_voucher(purchase_receipt("PR-1", at(1, 1), "X", WH, 10, 5))
    s.post_voucher(purchase_receipt("PR-2", at(1, 2), "X", WH, 10, 8))
    s.post_voucher(delivery_note("DN", at(1, 3), "X", WH, 12))
    s.edit_quantity("PR-1", 4)
    assert s.ledger.voucher_rows("DN")[0].computed.stock_value_difference == -(4 * 5 + 8 * 8)
    assert_consistent(s)


# ------------------------------------------------------------ safety nets
def test_integrity_check_detects_a_partially_applied_repost(task_service):
    from dataclasses import replace

    ledger = task_service.ledger
    row = ledger.voucher_rows("DN-0001")[0]
    row.computed = replace(row.computed, stock_value=row.computed.stock_value + 1)  # simulate drift
    problems = ledger.verify_running_balances()
    assert problems and any("DN-0001" in p for p in problems)


def test_validation_errors(task_service):
    s = task_service
    with pytest.raises(TypeError):
        purchase_receipt("PR-X", at(2, 1), "Milk", WH, 1, 0.1)       # floats rejected
    with pytest.raises(InvalidVoucherError):
        s.post_voucher(purchase_receipt("PR-0001", at(2, 1), "Milk", WH, 1, 1))  # duplicate no.
    with pytest.raises(UnknownVoucherError):
        s.edit_incoming_rate("DN-0001", 5)        # an issue has no editable rate
    with pytest.raises(UnknownVoucherError):
        s.cancel_voucher("NOPE")
    with pytest.raises(InvalidVoucherError):
        s.edit_quantity("PR-0001", -5)            # cannot flip a receipt into an issue
    with pytest.raises(InvalidVoucherError):
        s.edit_incoming_rate("PR-0001", -1)
