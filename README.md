# fifo-reposting

A small, tested reference implementation of **FIFO stock valuation reposting** for an
ERPNext-style stock ledger: what happens to the valuation of later transactions when an
earlier one is changed.

It solves the task *"Stock Reposting and Valuation Issue"* (Milk / Milk Powder) and
generalises it: edits, backdated entries, cancellations, manufacturing (cost flows from raw
material to finished goods), transfers and co-products.

* **Full written answer to the four questions:** [`docs/DESIGN.md`](docs/DESIGN.md)
* **Code:** `src/fifo_reposting/`
* **Tests:** `tests/` (95 tests, including a randomized differential test)

## The task scenario in 20 seconds

```
pip install -e ".[dev]"
python -m fifo_reposting          # runs the scenario and prints before / after
pytest -q
```

Editing the 10 Jan Purchase Receipt from 60 to 80 per litre:

| Voucher | Effect |
|---------|--------|
| `PR-0001` 10 Jan | **Revalued** (it is the edited row): 3,000 -> 4,000 |
| `DN-0001` 15 Jan | Only **rebalanced**: it took 80 L from the opening layer (@50), so its own value is unchanged; stock left behind is worth +1,000 |
| `PR-0002` 20 Jan | Only **rebalanced** (+1,000 running balance) |
| `STE-0001` 21 Jan | **Revalued**: milk consumed 5,800 -> 6,800; Milk Powder produced at 680/kg instead of 580 |
| `DN-0002` 25 Jan | **Revalued**: Milk Powder leaves at 680, COGS +1,000 |

The reposter visits 6 of the 7 ledger rows (everything except the opening stock) and writes
6. The same engine proves it stops early when a change is absorbed (see
`tests/test_reposter.py::test_replay_stops_once_the_queue_is_back_to_what_it_was`).

## Approach in brief

1. A row's valuation is a pure function of *(FIFO queue after the previous row of the same
   item and warehouse, the row's own inputs)*. Manufacture/transfer receipts additionally
   read the value their voucher consumed.
2. After a change, **replay forward from the first affected row**, starting from the stored
   queue of the row before it, in global posting order across all streams (a heap).
3. **Stop early** when a recomputed row leaves the queue exactly as stored, unless a later
   row has changed inputs of its own ("pinned").
4. **Propagate across items**: if an issue's value changed, the receipts derived from it
   (finished goods, transfer-in) are revalued, which continues the replay in their stream.
5. **Stage, then commit atomically.** Nothing is written during the replay; failure (e.g.
   negative stock) leaves the ledger untouched.
6. Each rewritten row is classified **REVALUED** (movement changed, GL must be reposted) or
   **REBALANCED** (only the running balance changed).

## Layout

```
src/fifo_reposting/
  fifo.py        immutable FIFO queue of cost layers (add / consume)
  models.py      Voucher, LineSpec, StockLedgerEntry, Computed
  ledger.py      storage, ordering, lookups, transactions, integrity check
  reposter.py    the incremental reposting algorithm
  service.py     public API: post_voucher / edit_incoming_rate / edit_quantity / cancel_voucher
  naive.py       independent full-rebuild oracle (used by tests; also an integrity job)
  vouchers.py    constructors: opening stock, receipt, delivery, manufacture, transfer
  scenario.py    the task's data;  report.py / __main__.py  pretty output
tests/           unit, scenario, behaviour and randomized differential tests
docs/DESIGN.md   answers to the four questions, design decisions, ERPNext mapping
```

## Usage

```python
from datetime import datetime
from fifo_reposting import StockService
from fifo_reposting.vouchers import purchase_receipt, delivery_note

svc = StockService()
svc.post_voucher(purchase_receipt("PR-1", datetime(2026, 1, 1), "Milk", "Stores", 100, 50))
svc.post_voucher(delivery_note("DN-1", datetime(2026, 1, 5), "Milk", "Stores", 40))

result = svc.edit_incoming_rate("PR-1", 55)   # the "user edits an old receipt" case
for change in result.changes:
    print(change.voucher_no, change.kind.value, change.value_delta)
```

## Design decisions worth knowing

* **Exact arithmetic (`Fraction`)**, not floats: early stop compares recomputed with stored
  state, which is only sound if identical inputs give identical outputs. It also removes
  rounding drift from the running balance.
* **Running balance is treated as a cache**, never as the source of truth. It can be verified
  (`ledger.verify_running_balances()`) and rebuilt (`full_rebuild`).
* **One code path** for posting, editing, cancelling and backdating.

## Limitations

FIFO only; no GL/Bin/batch/serial/UoM; in-memory storage; negative stock always rejected.
See section 6 of the design document and the ERPNext integration notes in section 3.4.
