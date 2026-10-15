# Design: FIFO stock reposting

This document answers the four questions of the task in depth and explains the design
decisions behind the code in `src/fifo_reposting/`.

## 0. The scenario and the numbers

Both items use FIFO. Dates are in 2026.

| Date | Voucher | Item | Movement |
|------|---------|------|----------|
| 01 Jan | OPEN-0001 | Milk | opening stock 100 L @ 50 |
| 10 Jan | PR-0001 | Milk | receipt 50 L @ **60** (later edited to **80**) |
| 15 Jan | DN-0001 | Milk | delivery 80 L |
| 20 Jan | PR-0002 | Milk | receipt 50 L @ 60 |
| 21 Jan | STE-0001 | Milk -> Milk Powder | manufacture: consume 100 L, produce 10 kg |
| 25 Jan | DN-0002 | Milk Powder | delivery 10 kg |

**Milk ledger before the edit** (FIFO queue = oldest layer first):

| Date | Voucher | Qty | Rate | Value chg | Bal qty | Bal value | Queue after |
|------|---------|----:|-----:|----------:|--------:|----------:|-------------|
| 01 Jan | OPEN-0001 | +100 | 50 | +5,000 | 100 | 5,000 | 100@50 |
| 10 Jan | PR-0001 | +50 | 60 | +3,000 | 150 | 8,000 | 100@50, 50@60 |
| 15 Jan | DN-0001 | -80 | 50 | -4,000 | 70 | 4,000 | 20@50, 50@60 |
| 20 Jan | PR-0002 | +50 | 60 | +3,000 | 120 | 7,000 | 20@50, 100@60 |
| 21 Jan | STE-0001 | -100 | 58 | -5,800 | 20 | 1,200 | 20@60 |

Milk Powder: 21 Jan +10 kg @ 580 (=5,800), 25 Jan -10 kg @ 580 (=-5,800).

**Milk ledger after changing PR-0001 to 80:**

| Date | Voucher | Qty | Rate | Value chg | Bal qty | Bal value | Queue after |
|------|---------|----:|-----:|----------:|--------:|----------:|-------------|
| 01 Jan | OPEN-0001 | +100 | 50 | +5,000 | 100 | 5,000 | 100@50 |
| 10 Jan | PR-0001 | +50 | **80** | **+4,000** | 150 | **9,000** | 100@50, 50@80 |
| 15 Jan | DN-0001 | -80 | 50 | -4,000 | 70 | **5,000** | 20@50, 50@80 |
| 20 Jan | PR-0002 | +50 | 60 | +3,000 | 120 | **8,000** | 20@50, 50@80, 50@60 |
| 21 Jan | STE-0001 | -100 | **68** | **-6,800** | 20 | 1,200 | 20@60 |

The 21 Jan manufacture now consumes `20 x 50 + 50 x 80 + 30 x 60 = 1,000 + 4,000 + 1,800 = 6,800`.
Milk Powder becomes 10 kg @ **680** (was 580), and the 25 Jan delivery goes out at 680
(value -6,800 instead of -5,800).

Run it yourself: `python -m fifo_reposting`.

---

## 1. Which stock transactions are affected?

The change is a rate change on one *layer* of Milk (the 50 L received on 10 Jan). A change
travels only along the transactions that **consume that layer or depend on something that
did**. It is useful to split the effect into three classes.

### a) Transactions whose valuation (rate / value / GL) changes

| Voucher | Why |
|---------|-----|
| **PR-0001** (10 Jan) | It is the edited document: rate 60 -> 80, value 3,000 -> 4,000. |
| **STE-0001** (21 Jan) | The manufacture is the first issue that reaches the 10 Jan layer: consumption value 5,800 -> 6,800. Its **finished good** (Milk Powder) is valued at the cost of the raw material, so the *incoming* rate of the powder also changes, 580 -> 680, even though the powder is a different item. |
| **DN-0002** (25 Jan) | The Milk Powder delivery issues the powder at its FIFO layer rate: 580 -> 680, COGS +1,000. |

These are the vouchers whose **General Ledger** entries must be reposted:
PR-0001 (Stock In Hand / Stock Received But Not Billed), STE-0001 (raw material out, finished
good in), DN-0002 (COGS / Stock In Hand).

### b) Transactions whose *running balance* changes but whose movement does not

| Voucher | What changes |
|---------|--------------|
| **DN-0001** (15 Jan) | Still issues 80 L @ 50 (all from the opening layer), but the stock *left behind* is worth 1,000 more (the 50 L layer is now @ 80). `stock_value` 4,000 -> 5,000, queue changes. |
| **PR-0002** (20 Jan) | Still +50 L @ 60; balance value 7,000 -> 8,000. |

These rows must be rewritten (they hold a stored `stock_value` / queue) but they produce
**no GL difference**: what they moved is unchanged.

### c) Not affected

* **OPEN-0001** and everything before the edited row: it cannot depend on the future.
* Quantities everywhere: a rate change never changes `qty_after_transaction`.
* Closing Milk stock: 20 L @ 60 = 1,200 before and after. The extra 1,000 that sat in
  inventory between 10 and 21 Jan has moved into Milk Powder and from there into COGS.

### Why DN-0001 is *not* revalued (and why this is a FIFO-specific answer)

Under FIFO the cost travels with the layer. The 15 Jan delivery took 80 L from the
*opening* layer (100 L @ 50) and never touched the 10 Jan layer. Under **Moving Average**
the answer would differ: the average after 10 Jan would be 60 instead of 53.33, and DN-0001
would be revalued too. The same code path handles both; only the "what is the rate of this
issue" function differs.

### Sanity check

Net effect on *Stock In Hand* after everything: +1,000 (PR-0001) - 1,000 extra (STE-0001
consuming more milk) + 1,000 (powder receipt) - 1,000 (DN-0002) = **0** now, and the
**COGS +1,000**. The system is internally consistent: the cost difference ended up in the
P&L at the moment the dearer layer was sold.

---

## 2. How should the subsequent transactions be recalculated?

A ledger row's valuation is a **pure function of two things**:

1. the FIFO queue *after the previous row of the same item + warehouse*, and
2. the row's own inputs (qty, rate) - plus, for derived-rate receipts such as manufacture
   output, the value consumed by the other lines of the same voucher.

So reposting is: *start from a known-good queue, replay the rows in posting order, and
update each row from the queue it receives*.

For the task, in order:

1. **Choose the starting queue.** The last row before the change is OPEN-0001, whose stored
   queue is `100@50`. (ERPNext stores this as `stock_queue` on the Stock Ledger Entry.)
2. **PR-0001**: `add(50, 80)` -> `[100@50, 50@80]`, value +4,000.
3. **DN-0001**: `consume(80)` takes 80 from the oldest layer, value 4,000 (80 x 50) ->
   `[20@50, 50@80]`.
4. **PR-0002**: `add(50, 60)` -> `[20@50, 50@80, 50@60]`.
5. **STE-0001 (issue line)**: `consume(100)` = 20@50 + 50@80 + 30@60 = **6,800** -> `[20@60]`.
6. **STE-0001 (receipt line)**: rate = consumed value / quantity produced = 6,800 / 10 = **680**.
   This is the cross-item hand-off: Milk's repost *discovers* that Milk Powder must be
   reposted from 21 Jan.
7. **DN-0002**: `consume(10)` on powder `[10@680]` -> value 6,800.
8. **Repost the GL** for every voucher classified as *revalued* (section 1a), and refresh
   the item/warehouse summary (`Bin` in ERPNext).

Two ordering rules make this correct:

* **Total order** within a stream: `(posting datetime, creation, line number)`. Two vouchers
  at the same timestamp are ordered by creation; lines in one voucher by line number.
* **Issues before receipts inside a voucher.** A derived-rate receipt reads the voucher's
  issues, so they must be valued first.

---

## 3. Designing the reposting logic for FIFO

### 3.1 Naive version and why it is not enough

Rebuild the entire ledger from the first row (`naive.full_rebuild`). It is trivially correct
and is kept in the repo as a test oracle and as an integrity job ("rebuild and compare"). But
it costs O(all rows of the site) for every small edit, so it cannot be the production path.

### 3.2 Incremental algorithm (`reposter.py`)

```
seeds := rows whose own inputs changed (edited / new / the row after a cancelled one)
heap  := one cursor per stream, ordered by global posting order

while heap not empty:
    row     := pop the smallest cursor            # global time order across all streams
    queue_in := queue stored after the previous row of this stream
    new     := value(row, queue_in)               # FIFO add / consume (+ derived rate)

    stage new if it differs from what is stored    # nothing is written yet

    if row is an issue and its movement changed:
        pin the derived receipts of its voucher    # may live in ANOTHER stream
    if new.queue != stored.queue:
        continue the stream with the next row      # later rows start from a different queue
    else:
        jump to the next pinned row, or stop       # CONVERGED: nothing after can change

commit all staged rows in one step
```

Key properties:

* **Early stop (convergence).** If a recomputed row leaves the queue exactly as stored,
  every later row of that stream sees the same input and is unchanged. Example from the
  tests: receipt A (10 @ 5) is edited to 9; the delivery of 10 on 3 Jan consumes exactly A's
  layer, so the queue after it is `[10@6]` either way. Rows from 4 Jan on are not read or
  written. This is the main reason it beats a full rebuild: the cost is proportional to the
  *blast radius* of the change, not to the age of the ledger.
* **Cross-item propagation.** If an issue's value changed, the derived receipts of that
  voucher change, and from there the receiving item's stream. Because the heap processes all
  streams in global posting order and a dependency always points to an *earlier* key (or an
  earlier line of the same voucher), the dependency graph is acyclic and one sweep is enough.
* **Pinned rows.** Early stop is only valid if no later row has *changed inputs of its own*.
  Such rows (seeds, derived receipts) are pinned; a stream that converges jumps to the next
  pinned row instead of stopping.
* **REVALUED vs REBALANCED.** Each rewritten row is classified. *Revalued* = the movement's
  rate/value changed (needs GL repost). *Rebalanced* = only running balance changed (no GL
  work). This is the precise answer to question 1 and it lets the GL repost be minimal.
* **Atomic.** Results are staged in memory and committed in one step. A failure (e.g.
  negative stock discovered at row N) leaves the ledger exactly as it was, and
  `StockLedger.transaction()` rolls back the input mutation too.
* **One code path.** Posting a new voucher, backdating, editing and cancelling are all
  "seed some rows, repost". There is no separate append path whose rules could diverge.

### 3.3 Complexity

Let `k` be the number of rows visited (rows from the change until the queue converges, plus
dependent streams).

* Time: O(k log s) for the heap over `s` active streams, plus O(layers) per row.
* Writes: only rows whose stored values actually differ.
* Worst case (the change is in the oldest layer and every later issue touches it): the whole
  stream - same as a full rebuild, never worse.

### 3.4 How this maps onto ERPNext in production

ERPNext already has the right shape; the logic above is what "good" looks like inside it.
(Names below are from memory of the v14/v15 code base; verify against your version.)

| Concern | Suggested handling |
|---------|--------------------|
| Trigger | A `Repost Item Valuation` document (by transaction, or by item + warehouse) created when a submitted voucher changes. |
| Execution | Background job, one at a time per `(item, warehouse)`; skip/merge duplicate requests for the same stream and an earlier-or-equal starting point. |
| Starting point | The earliest affected `(posting_datetime, creation)`; load the stored `stock_queue` of the row before it. |
| Concurrency | Serialize with a lock on `(item, warehouse)` while posting *and* reposting, so an in-flight repost cannot interleave with a new Stock Ledger Entry. |
| Atomicity | Write the repost in one DB transaction, or in chunks with a persisted resume position and an idempotent per-row update. Never leave a stream half-updated and visible. |
| After SLE repost | Repost GL entries of the vouchers classified REVALUED, then update `Bin`. |
| Negative stock | If a repost would drive stock below zero and negative stock is disallowed, fail the repost with the voucher and shortfall; do not partially apply. |
| Observability | Log rows visited / rewritten, duration and status on the repost document. |

---

## 4. What can go wrong when every Stock Ledger Entry stores a running balance?

Persisting `qty_after_transaction`, `stock_value` (and for FIFO a `stock_queue` snapshot) on
every row makes *reads* cheap (the balance at any point is one row) but turns the ledger into
a **chain of derived values** where each row depends on the previous one. Consequences:

1. **Write amplification.** One change to an old row can rewrite every later row of the
   stream (millions for a busy item). Long transactions, large undo/redo logs, lock waits.
   *Mitigation here:* early stop + write only rows whose values differ.
2. **Visible inconsistency window.** While a repost runs, early rows are new and later rows
   are old. Reports and the negative-stock check read mixed balances.
   *Mitigation:* stage and commit atomically, or mark the stream "reposting" and block
   reads/posting for it until done.
3. **Lost updates / races.** Two postings that read "the latest balance" at the same time
   both compute their balance from the same predecessor; one silently overwrites the other.
   Backdated entries make it worse (the "latest" row is not last in time).
   *Mitigation:* per-`(item, warehouse)` lock, deterministic total order.
4. **Partial failure.** A crash or timeout midway leaves a prefix updated and a suffix stale.
   The ledger no longer adds up, and nothing flags it.
   *Mitigation:* single-transaction commit (this repo), or resumable idempotent chunks, plus
   an integrity check (`verify_running_balances`).
5. **Drift between running balance and truth.** With floats or per-row rounding, repeated
   incremental updates accumulate error: the stored `stock_value` stops equalling the sum of
   the movements or the FIFO queue, and the stock account stops reconciling with the GL.
   *Mitigation:* exact arithmetic or fixed-point integers (this repo uses `Fraction`), and a
   periodic "rebuild and compare" job (`naive.full_rebuild`).
6. **Bulky snapshots.** Storing the FIFO queue on every row duplicates it thousands of
   times and makes "did anything change?" comparisons expensive.
   *Mitigation:* store a snapshot every N rows (checkpoint) and replay from the nearest one,
   or a compact canonical encoding (this repo merges equal-rate layers so equal stock compares
   equal).
7. **Ordering ambiguity.** Same-timestamp vouchers, or a posting time edited after the fact,
   make the "previous row" ill-defined, so balances differ depending on insertion order.
   *Mitigation:* an explicit total order `(posting_datetime, creation, line)` and a repost
   from the *earlier* of old and new position when a row moves.
8. **Poor parallelism.** Row N needs row N-1, so one stream's repost is inherently
   sequential. Only independent streams can run in parallel (cross-item dependencies from
   manufacturing still force ordering between the streams involved).
9. **Two sources of truth.** The movement (qty, rate) and the balance can disagree after a
   bug or manual SQL. Without a defined *authority* nobody knows which is right.
   *Decision here:* the movements and ordering are the truth; every balance is a cache that
   can be thrown away and rebuilt.

A design that avoids most of these is **not to persist the balance per row** at all, and
compute it on read from per-stream checkpoints. ERPNext keeps the per-row balance (it makes
reports and validations fast), so the pragmatic approach is the one implemented here:
keep it, treat it as a cache, repost incrementally and atomically, and verify it.

---

## 5. Design decisions

| Decision | Reason |
|----------|--------|
| `Fraction` for all numbers | Early stop compares recomputed vs stored state; this is only sound if the same inputs give identical outputs. Exact arithmetic also removes drift (problem 5). Rounding is applied only when displaying. At the database boundary you would use Decimal/fixed-point with the site's precision settings. |
| Immutable `FifoQueue`, canonical form | A snapshot after row N is reusable as the start state for row N+1 without copying or aliasing bugs; canonical merging makes "same stock" compare equal. |
| Stage then commit | Atomic repost; failures leave no trace. |
| Single repost path for post / edit / cancel / backdate | Fewer special cases, more coverage per test. |
| Seed the whole voucher when editing a line | A voucher is valued as a unit; seeding all lines up front keeps the global replay order correct. Rows that do not change stop the replay immediately, so it is nearly free. |
| Issues sort before receipts within a voucher | Derived-rate receipts can read already-valued issues. |
| Transfer-in forms *one* layer at the blended rate | The receiving side is valued at the source's outgoing rate (value / qty), as ERPNext does. Layer identity is therefore not preserved across warehouses. |
| In-memory ledger | The task is about the algorithm. `StockLedger` is the only storage abstraction; the Reposter only uses its small read API plus `commit`. |

---

## 6. Limitations / non-goals

* Moving Average and LIFO are not implemented (the replay machinery would be reused; only
  the issue-valuation function differs).
* GL entries, `Bin`, batches/serial numbers, UoM conversion, landed cost and
  `allow_zero_valuation_rate` are out of scope. `RowChange.value_delta` is the hook a GL
  repost would use.
* Negative stock is always rejected. ERPNext can allow it with its own valuation rules.
* Moving a voucher to a different posting date is not exposed; it would seed both the old
  and the new position.
* Single-process, single-threaded. Section 3.4 describes locking/queueing for production.

---

## 7. Testing strategy

* **Unit** (`test_fifo.py`): queue behaviour, exactness, immutability.
* **Scenario** (`test_scenario.py`): every number of the task, before and after, plus the
  classification of affected vouchers and the exact GL deltas.
* **Behaviour** (`test_reposter.py`): early stop, no-op edit, backdating, cancellation,
  atomic rollback on negative stock, multi-stage manufacturing, transfers, co-products,
  repacks, integrity-check detection.
* **Differential / property** (`test_equivalence.py`): random histories of receipts,
  deliveries, manufactures, repacks, transfers, rate/qty edits and cancellations. After
  *every* operation the incremental ledger must equal the independent full rebuild, and every
  rejected operation must leave the ledger unchanged.

The differential test earned its keep: it found a real bug during development. When a voucher
had two lines in the same stream (a repack that consumes and re-produces one item) and the
earlier line was stable, the replay stopped before reaching the later, edited line. The fix is
that every seed is *pinned* (section 3.2), and there is now a deterministic regression test.
Deliberately breaking the early-stop or propagation logic makes the suite fail, so the tests
are sensitive to exactly the logic that matters.
