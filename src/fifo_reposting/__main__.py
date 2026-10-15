"""``python -m fifo_reposting`` - run the task scenario and print before/after."""

from __future__ import annotations

import argparse

from .numbers import format_number
from .report import render_ledger, render_result
from .scenario import EDITED_VOUCHER, MILK, build_task_ledger


def main() -> None:
    parser = argparse.ArgumentParser(description="FIFO reposting demo (Milk / Milk Powder scenario).")
    parser.add_argument("--new-rate", default="80", help="new rate for the 10 Jan receipt (default: 80)")
    args = parser.parse_args()

    service = build_task_ledger()
    print(f"=== BEFORE: {EDITED_VOUCHER} (10 Jan) at 60/L ===")
    print(render_ledger(service.ledger))

    result = service.edit_incoming_rate(EDITED_VOUCHER, args.new_rate)

    print(f"\n=== AFTER: {EDITED_VOUCHER} edited to {args.new_rate}/L ===")
    print(render_ledger(service.ledger))
    print("\n=== WHAT THE REPOST DID ===")
    print(render_result(result))

    problems = service.ledger.verify_running_balances()
    print("\nrunning-balance check:", "OK" if not problems else problems)
    # Issues to customers are what hit COGS: more negative value moved out = more COGS.
    extra_cogs = -sum(c.value_delta for c in result.changes
                      if c.voucher_type == "Delivery Note")
    print(f"extra COGS booked by the reposted Delivery Notes: {format_number(extra_cogs)}"
          f"   (closing {MILK} stock value is unchanged)")


if __name__ == "__main__":
    main()
