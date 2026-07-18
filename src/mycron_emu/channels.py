#!/usr/bin/env python

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")

log = logging.getLogger("mycron.channels")


class Channel(Generic[T]):
    """A synchronous in-process fan-out channel."""

    def __init__(self, name: str):
        self.name = name
        self._listeners: dict[int, Callable[[T], None]] = {}
        self._next_id = 0

    def subscribe(self, listener: Callable[[T], None])-> Callable[[], None]:
        """Subscribe and return a function that unsubscribes the listener."""

        listener_id = self._next_id
        self._next_id += 1
        self._listeners[listener_id] = listener

        def unsubscribe() -> None:
            self._listeners.pop(listener_id, None)

        return unsubscribe

    def send(self, message: T) -> None:
        """Send a message to every current listener."""

        # Snapshot the values so listeners may safely unsubscribe while
        # processing a message.
        for listener in tuple(self._listeners.values()):
            try:
                listener(message)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                # One failed monitor should not prevent other monitors from
                # receiving guest output.
                log.exception(
                    "Listener failed on channel %s",
                    self.name,
                )


class ChannelRegistry:
    def __init__(self):
        self._channels: dict[str, Channel] = {}

    def create(self, name: str) -> Channel:
        if name in self._channels:
            raise ValueError(f"channel already exists: {name!r}")

        channel = Channel(name)
        self._channels[name] = channel
        return channel

    def get(self, name: str) -> Channel:
        try:
            return self._channels[name]
        except KeyError:
            raise KeyError(f"unknown channel: {name!r}") from None
