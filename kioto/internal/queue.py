"""
Async queue utilities for internal use.

Provides SlotQueue for slot-based async queuing with reservation semantics.
"""

import asyncio
import collections


class SlotQueue:
    """
    An asynchronous queue built on top of a collections.deque with slot reservation and peek support.

    API:
      - `async with queue.put() as slot:`
            Reserve a slot in the queue, then set the value via `slot.value = ...`
      - `async with queue.get() as slot:`
            Wait until an item is available; then access the item via `slot.value`
            (the item is only removed after exiting the context).

    The queue has a fixed capacity. A slot is reserved via put() only if there is space
    (i.e. the total number of committed items is less than the capacity). Consumers using get()
    wait until at least one committed item is available.
    """

    def __init__(self, capacity: int):
        self._capacity = capacity
        self._items = collections.deque()  # Holds committed items.
        self._lock = asyncio.Lock()
        # Condition for waiting until there is room for a new item.
        self._not_full = asyncio.Condition(self._lock)
        # Condition for waiting until an item is available.
        self._not_empty = asyncio.Condition(self._lock)

    def put(self):
        """
        Returns an async context manager that reserves a slot in the queue.

        Usage:
            async with queue.put() as slot:
                slot.value = <your value>
        """
        return _PutSlot(self)

    def get(self):
        """
        Returns an async context manager that waits for an item to be available.

        Usage:
            async with queue.get() as slot:
                item = slot.value  # The item is available to be peeked at.
        When the context is exited, the item is popped.
        """
        return _GetSlot(self)


class _PutSlot:
    """
    Async context manager for putting an item into an SlotQueue.

    Upon __aenter__, it waits until a free slot is available (i.e. there is room in the queue).
    Then the caller can set its `value` attribute.

    Upon __aexit__, if no exception occurred the value is committed to the queue, and
    waiting consumers are notified.
    """

    __slots__ = ("_queue", "value", "_reserved")

    def __init__(self, queue: SlotQueue):
        self._queue = queue
        self.value = None
        self._reserved = False

    async def __aenter__(self):
        async with self._queue._not_full:
            while len(self._queue._items) >= self._queue._capacity:
                await self._queue._not_full.wait()
            self._reserved = True
            return self

    async def __aexit__(self, exc_type, exc, tb):
        # If no exception occurred, commit the value.
        if exc_type is None:
            async with self._queue._not_empty:
                self._queue._items.append(self.value)
                self._queue._not_empty.notify_all()
        # Regardless, release the reservation and notify producers waiting for space.
        self._reserved = False
        async with self._queue._not_full:
            self._queue._not_full.notify_all()
        return False  # Do not suppress exceptions.


class _GetSlot:
    """
    Async context manager for getting an item from an SlotQueue.

    Upon __aenter__, it waits until an item is available and then returns a slot object
    whose `value` attribute is the next item in the queue (without removing it).

    Upon __aexit__, the item is removed from the queue and producers waiting for space
    are notified.
    """

    __slots__ = ("_queue", "value")

    def __init__(self, queue: SlotQueue):
        self._queue = queue
        self.value = None

    async def __aenter__(self):
        async with self._queue._not_empty:
            while not self._queue._items:
                await self._queue._not_empty.wait()
            # Peek at the first item without removing it.
            self.value = self._queue._items[0]
            return self

    async def __aexit__(self, exc_type, exc, tb):
        async with self._queue._not_empty:
            # Remove the first item (that was peeked) from the queue.
            self._queue._items.popleft()
            self._queue._not_full.notify_all()
        return False  # Do not suppress exceptions.
