from fractions import Fraction as F

import pytest

from fifo_reposting import FifoQueue, InsufficientQuantityError
from fifo_reposting.numbers import format_number, to_number


def test_receipts_append_layers_oldest_first():
    queue = FifoQueue.of([(100, 50), (50, 60)])
    assert [(l.qty, l.rate) for l in queue.layers] == [(100, 50), (50, 60)]
    assert queue.qty == 150
    assert queue.value == 8000


def test_adjacent_layers_with_equal_rate_are_merged():
    assert FifoQueue.of([(10, 5), (10, 5)]) == FifoQueue.of([(20, 5)])


def test_consume_takes_oldest_layer_first_and_values_it():
    queue = FifoQueue.of([(100, 50), (50, 60)])
    after, value = queue.consume(80)
    assert value == 80 * 50
    assert after == FifoQueue.of([(20, 50), (50, 60)])


def test_consume_spanning_layers():
    queue = FifoQueue.of([(20, 50), (50, 60), (50, 60)])  # merged into 20@50, 100@60
    after, value = queue.consume(100)
    assert value == 20 * 50 + 80 * 60
    assert after == FifoQueue.of([(20, 60)])


def test_consume_exactly_everything_leaves_empty_queue():
    after, value = FifoQueue.of([(3, 7)]).consume(3)
    assert after == FifoQueue()
    assert value == 21


def test_consume_more_than_available_raises_with_shortfall():
    with pytest.raises(InsufficientQuantityError) as info:
        FifoQueue.of([(5, 10)]).consume(8)
    assert info.value.shortfall == 3


def test_queue_is_immutable():
    queue = FifoQueue.of([(10, 5)])
    queue.consume(4)
    queue.add(F(1), F(9))
    assert queue == FifoQueue.of([(10, 5)])


def test_non_positive_quantities_are_rejected():
    with pytest.raises(ValueError):
        FifoQueue().add(F(0), F(1))
    with pytest.raises(ValueError):
        FifoQueue.of([(1, 1)]).consume(F(0))


def test_exact_arithmetic_has_no_rounding_error():
    # 10 units costing 1/3 each is exactly 10/3 - floats could not say that.
    queue = FifoQueue.of([(10, F(1, 3))])
    assert queue.value == F(10, 3)
    after, value = queue.consume(3)
    assert value == 1
    assert after.value == F(7, 3)


def test_to_number_accepts_exact_types_and_rejects_floats():
    assert to_number("0.1") == F(1, 10)
    assert to_number(3) == 3
    with pytest.raises(TypeError):
        to_number(0.1)


def test_format_number_rounds_half_up():
    assert format_number(F(1, 3)) == "0.33"
    assert format_number(F(5, 2), 0) == "3"
    assert format_number(F(2, 3)) == "0.67"
