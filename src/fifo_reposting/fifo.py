"""Immutable FIFO queue of cost layers.

A layer is "``qty`` units that cost ``rate`` each". Receipts append a layer; issues
consume from the oldest layer first. The queue is immutable so that a snapshot taken
after one ledger row can be reused safely as the starting point for the next row, and
it is kept in *canonical form* (adjacent layers with equal rates are merged) so that
two queues describing the same stock compare equal. That equality is what lets the
reposter stop early.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable, Tuple

from .errors import InsufficientQuantityError


@dataclass(frozen=True)
class Layer:
    qty: Fraction
    rate: Fraction

    @property
    def value(self) -> Fraction:
        return self.qty * self.rate


@dataclass(frozen=True)
class FifoQueue:
    layers: Tuple[Layer, ...] = ()

    @classmethod
    def of(cls, layers: Iterable[Tuple[Fraction, Fraction]]) -> "FifoQueue":
        """Build a queue from ``(qty, rate)`` pairs, oldest first."""
        queue = cls()
        for qty, rate in layers:
            queue = queue.add(Fraction(qty), Fraction(rate))
        return queue

    @property
    def qty(self) -> Fraction:
        return sum((layer.qty for layer in self.layers), Fraction(0))

    @property
    def value(self) -> Fraction:
        return sum((layer.value for layer in self.layers), Fraction(0))

    def add(self, qty: Fraction, rate: Fraction) -> "FifoQueue":
        """Return a new queue with ``qty`` units at ``rate`` appended as the newest layer."""
        if qty <= 0:
            raise ValueError("a receipt must have a positive quantity")
        if self.layers and self.layers[-1].rate == rate:
            last = self.layers[-1]
            return FifoQueue(self.layers[:-1] + (Layer(last.qty + qty, rate),))
        return FifoQueue(self.layers + (Layer(qty, rate),))

    def consume(self, qty: Fraction) -> Tuple["FifoQueue", Fraction]:
        """Issue ``qty`` units oldest-first.

        Returns ``(queue_after, value_of_units_issued)``.

        Raises:
            InsufficientQuantityError: if the queue holds less than ``qty``.
        """
        if qty <= 0:
            raise ValueError("an issue must have a positive quantity")
        remaining = qty
        value = Fraction(0)
        layers = self.layers
        index = 0
        while remaining > 0:
            if index == len(layers):
                raise InsufficientQuantityError(remaining)
            layer = layers[index]
            take = min(layer.qty, remaining)
            value += take * layer.rate
            remaining -= take
            if take == layer.qty:
                index += 1
            else:
                layers = layers[:index] + (Layer(layer.qty - take, layer.rate),) + layers[index + 1:]
        return FifoQueue(layers[index:]), value

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        if not self.layers:
            return "[]"
        return "[" + ", ".join(f"{l.qty}@{l.rate}" for l in self.layers) + "]"
