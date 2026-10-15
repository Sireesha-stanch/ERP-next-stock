"""Differential test: random histories, incremental result must equal a full rebuild.

After *every* operation the incrementally maintained ledger is compared with the
independent from-scratch oracle. Operations that are legitimately rejected (negative
stock) must leave the ledger bit-for-bit unchanged.
"""

import random
from datetime import datetime, timedelta

import pytest

from fifo_reposting import (
    InvalidVoucherError, NegativeStockError, StockService, UnknownVoucherError, full_rebuild,
)
from fifo_reposting.models import RateMode
from fifo_reposting.vouchers import (
    delivery_note, manufacture, material_transfer, opening_stock, purchase_receipt,
)

ITEMS = ("A", "B", "C")
WAREHOUSES = ("W1", "W2")
BASE = datetime(2026, 1, 1, 8, 0)


def random_time(rng):
    return BASE + timedelta(days=rng.randint(0, 20), hours=rng.choice((0, 0, 1, 5)))


def random_operation(rng, service, counter):
    ledger = service.ledger
    vouchers = sorted({r.voucher_no for r in ledger.rows_in_order()})
    n = next(counter)
    kind = rng.choices(
        ["receipt", "delivery", "manufacture", "repack", "transfer", "edit_rate", "edit_qty", "cancel"],
        weights=[22, 18, 12, 8, 8, 14, 10, 12],
    )[0]
    item, wh = rng.choice(ITEMS), rng.choice(WAREHOUSES)

    if kind == "receipt":
        return lambda: service.post_voucher(
            purchase_receipt(f"PR-{n}", random_time(rng), item, wh, rng.randint(1, 20), rng.randint(1, 99)))
    if kind == "delivery":
        return lambda: service.post_voucher(
            delivery_note(f"DN-{n}", random_time(rng), item, wh, rng.randint(1, 15)))
    if kind == "manufacture":
        raw, product = rng.sample(ITEMS, 2)
        return lambda: service.post_voucher(manufacture(
            f"MF-{n}", random_time(rng), [(raw, wh, rng.randint(1, 12))], [(product, wh, rng.randint(1, 6))]))
    if kind == "repack":  # consumes two items, re-produces one of them (same voucher, same stream)
        a, b = rng.sample(ITEMS, 2)
        return lambda: service.post_voucher(manufacture(
            f"RP-{n}", random_time(rng), [(b, wh, rng.randint(1, 8)), (a, wh, rng.randint(1, 8))],
            [(a, wh, rng.randint(1, 8))]))
    if kind == "transfer":
        src, dst = rng.sample(WAREHOUSES, 2)
        return lambda: service.post_voucher(
            material_transfer(f"MT-{n}", random_time(rng), item, src, dst, rng.randint(1, 10)))

    if not vouchers:
        return lambda: None
    target = rng.choice(vouchers)
    if kind == "edit_rate":
        return lambda: service.edit_incoming_rate(target, rng.randint(1, 120))
    if kind == "edit_qty":
        lines = ledger.voucher_rows(target)
        line = rng.choice(lines)
        qty = rng.randint(1, 15)
        return lambda: service.edit_quantity(target, qty if line.qty > 0 else -qty, line_no=line.line_no)
    return lambda: service.cancel_voucher(target)


def counter_from(start=1):
    value = start
    while True:
        yield value
        value += 1


@pytest.mark.parametrize("seed", range(60))
def test_incremental_equals_full_rebuild(seed):
    rng = random.Random(seed)
    service = StockService()
    counter = counter_from()
    for wh in WAREHOUSES:  # seed some stock so that issues are usually feasible
        for item in ITEMS:
            service.post_voucher(opening_stock(f"OP-{item}-{wh}", BASE - timedelta(days=1), item, wh, 40, 10))

    applied = rejected = 0
    for _ in range(40):
        operation = random_operation(rng, service, counter)
        before = dict(service.ledger.snapshot())
        before_vouchers = {r.voucher_no for r in service.ledger.rows_in_order()}
        try:
            operation()
        except NegativeStockError:
            rejected += 1
            assert service.ledger.snapshot() == before, "failed operation must not change the ledger"
            assert {r.voucher_no for r in service.ledger.rows_in_order()} == before_vouchers
            continue
        except (InvalidVoucherError, UnknownVoucherError):
            continue  # e.g. random edit aimed at a line that cannot be edited that way
        applied += 1
        assert service.ledger.snapshot() == full_rebuild(service.ledger)
        assert service.ledger.verify_running_balances() == []

    assert applied > 10  # the run exercised real work, not just rejections


def test_derived_receipts_exist_in_random_runs():
    """Guard against the generator silently never producing manufacture/transfer vouchers."""
    rng = random.Random(1)
    service = StockService()
    counter = counter_from()
    for wh in WAREHOUSES:
        for item in ITEMS:
            service.post_voucher(opening_stock(f"OP-{item}-{wh}", BASE - timedelta(days=1), item, wh, 40, 10))
    for _ in range(60):
        try:
            random_operation(rng, service, counter)()
        except (NegativeStockError, InvalidVoucherError, UnknownVoucherError):
            pass
    assert any(r.rate_mode is RateMode.FROM_CONSUMPTION for r in service.ledger.rows_in_order())
