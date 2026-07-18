#!/usr/bin/env python

from __future__ import annotations

import heapq
import itertools
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar


T = TypeVar("T")


@dataclass(order=True)
class _ScheduledItem(Generic[T]):
    deadline: float
    sequence: int
    message: T = field(compare=False)


class ScheduledSender(Generic[T]):
    """Deliver messages to a callable when their deadlines are reached.

    The scheduler does not run a thread. The application must call poll()
    periodically.
    """
    def __init__(self, destination: Callable[[T], None], *,
                 clock: Callable[[], float] = time.monotonic):
        self._destination = destination
        self._clock = clock
        self._items: list[_ScheduledItem[T]] = []
        self._sequence = itertools.count()

    def schedule_after(self, delay: float, message: T) -> None:
        """Schedule a message relative to the current time."""

        delay = float(delay)

        if delay < 0:
            raise ValueError("delay must be non-negative")

        self.schedule_at(self._clock() + delay, message)

    def schedule_at(self, deadline: float, message: T) -> None:
        """Schedule a message at an absolute clock value."""

        heapq.heappush(
            self._items,
            _ScheduledItem(deadline=float(deadline), sequence=next(self._sequence), message=message))

    def poll(self, now: float | None = None) -> int:
        """Deliver all due messages and return the number delivered."""

        if now is None:
            now = self._clock()

        delivered = 0

        while self._items and self._items[0].deadline <= now:
            item = heapq.heappop(self._items)
            self._destination(item.message)
            delivered += 1

        return delivered

    def clear(self) -> None:
        self._items.clear()

    def __bool__(self) -> bool:
        return bool(self._items)

    @property
    def next_deadline(self) -> float | None:
        if not self._items:
            return None

        return self._items[0].deadline
