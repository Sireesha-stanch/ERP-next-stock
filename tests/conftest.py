from datetime import datetime

import pytest

from fifo_reposting.scenario import build_task_ledger


@pytest.fixture
def task_service():
    """The task's ledger *before* the 10 Jan receipt is edited."""
    return build_task_ledger()


def at(month: int, day: int, hour: int = 10) -> datetime:
    return datetime(2026, month, day, hour, 0)
