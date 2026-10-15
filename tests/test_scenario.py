"""The exact scenario from the task, with hand-computed expectations."""

from fractions import Fraction as F

from fifo_reposting import ChangeKind, FifoQueue, full_rebuild
from fifo_reposting.scenario import EDITED_VOUCHER, MILK, MILK_POWDER


def movement(service, voucher_no, item):
    """(rate, value of the movement) of one ledger row."""
    for row in service.ledger.voucher_rows(voucher_no):
        if row.item_code == item:
            return row.computed.valuation_rate, row.computed.stock_value_difference
    raise AssertionError(f"{voucher_no}/{item} not found")


def balance(service, voucher_no, item):
    for row in service.ledger.voucher_rows(voucher_no):
        if row.item_code == item:
            return row.computed.qty_after_transaction, row.computed.stock_value
    raise AssertionError


def test_before_the_edit(task_service):
    s = task_service
    assert movement(s, "DN-0001", MILK) == (50, -4000)          # 80 L all from the opening layer
    assert movement(s, "STE-0001", MILK) == (58, -5800)         # 20@50 + 50@60 + 30@60
    assert movement(s, "STE-0001", MILK_POWDER) == (580, 5800)  # powder carries the milk cost
    assert movement(s, "DN-0002", MILK_POWDER) == (580, -5800)
    assert balance(s, "STE-0001", MILK) == (20, 1200)           # 20 L @ 60 left
    assert s.ledger.verify_running_balances() == []


def test_after_the_edit_values(task_service):
    task_service.edit_incoming_rate(EDITED_VOUCHER, 80)
    s = task_service
    assert movement(s, "PR-0001", MILK) == (80, 4000)
    assert movement(s, "DN-0001", MILK) == (50, -4000)          # unchanged: FIFO never touched the 10 Jan layer
    assert movement(s, "PR-0002", MILK) == (60, 3000)           # unchanged
    assert movement(s, "STE-0001", MILK) == (68, -6800)         # 20@50 + 50@80 + 30@60
    assert movement(s, "STE-0001", MILK_POWDER) == (680, 6800)  # cost cascades into the product
    assert movement(s, "DN-0002", MILK_POWDER) == (680, -6800)  # ... and into COGS
    assert balance(s, "STE-0001", MILK) == (20, 1200)           # closing milk stock is unchanged
    assert s.ledger.verify_running_balances() == []


def test_question_1_which_transactions_are_affected(task_service):
    result = task_service.edit_incoming_rate(EDITED_VOUCHER, 80)

    # The movement itself is re-valued -> GL entries of these must be reposted.
    assert result.voucher_numbers(ChangeKind.REVALUED) == ["PR-0001", "STE-0001", "DN-0002"]
    # Only the running balance changes (stock value / queue) -> no GL impact.
    assert result.voucher_numbers(ChangeKind.REBALANCED) == ["DN-0001", "PR-0002"]
    # The opening stock is before the change and is never even read.
    touched = {c.voucher_no for c in result.changes}
    assert "OPEN-0001" not in touched
    assert result.rows_visited == 6 and result.rows_in_ledger == 7


def test_question_2_gl_impact_is_exactly_the_rate_difference(task_service):
    result = task_service.edit_incoming_rate(EDITED_VOUCHER, 80)
    deltas = {(c.voucher_no, c.item_code): c.value_delta for c in result.changes
              if c.kind is ChangeKind.REVALUED}
    assert deltas == {
        ("PR-0001", MILK): 1000,           # 50 L x 20 more
        ("STE-0001", MILK): -1000,         # consumed 1000 more of milk ...
        ("STE-0001", MILK_POWDER): 1000,   # ... and produced powder worth 1000 more
        ("DN-0002", MILK_POWDER): -1000,   # ... which leaves as 1000 more COGS
    }


def test_final_queues(task_service):
    task_service.edit_incoming_rate(EDITED_VOUCHER, 80)
    last_milk = task_service.ledger.stream_rows((MILK, "Stores"))[-1].computed.queue
    last_powder = task_service.ledger.stream_rows((MILK_POWDER, "Stores"))[-1].computed.queue
    assert last_milk == FifoQueue.of([(20, 60)])
    assert last_powder == FifoQueue()


def test_incremental_result_equals_full_rebuild(task_service):
    task_service.edit_incoming_rate(EDITED_VOUCHER, 80)
    assert task_service.ledger.snapshot() == full_rebuild(task_service.ledger)


def test_going_back_restores_the_original_ledger(task_service):
    original = dict(task_service.ledger.snapshot())
    task_service.edit_incoming_rate(EDITED_VOUCHER, 80)
    task_service.edit_incoming_rate(EDITED_VOUCHER, 60)
    assert task_service.ledger.snapshot() == original


def test_any_new_rate_matches_hand_formula(task_service):
    # Cost consumed on 21 Jan = 20*50 + 50*r + 30*60 for any new rate r.
    for rate in ("0", "45.5", "61", "100"):
        task_service.edit_incoming_rate(EDITED_VOUCHER, rate)
        value = F(rate)
        expected = 20 * 50 + 50 * value + 30 * 60
        assert movement(task_service, "STE-0001", MILK)[1] == -expected
        assert movement(task_service, "DN-0002", MILK_POWDER)[1] == -expected
