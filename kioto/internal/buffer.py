"""
Buffer pool module for efficient memory management with automatic lifecycle.

Provides BufferPool and ManagedBuffer classes that enable zero-copy operations
while automatically managing buffer lifetime through garbage collection.

Also includes SlotQueue for internal async queue operations with slot reservation.
"""

import asyncio
import collections
import weakref
from typing import Optional


class BufferPool:
    """
    Pool of reusable buffers with automatic lifecycle management.

    Maintains a pool of fixed-size buffers that can be borrowed and automatically
    returned when no longer referenced. When the pool is empty, new buffers are
    allocated. When the pool is full, excess buffers are discarded.
    """

    def __init__(self, buffer_size: int, max_pool_size: int = 10):
        """
        Create a buffer pool.

        Args:
            buffer_size: Size of each buffer in the pool
            max_pool_size: Maximum number of buffers to keep in pool
        """
        self._buffer_size = buffer_size
        self._max_pool_size = max_pool_size
        self._available = []  # Available buffers ready for reuse

    def get_buffer(self) -> 'ManagedBuffer':
        """
        Get a buffer from pool, allocating new one if pool is empty.

        Returns:
            ManagedBuffer: A managed buffer that will be automatically returned to pool
        """
        if self._available:
            raw_buffer = self._available.pop()
        else:
            raw_buffer = bytearray(self._buffer_size)

        return ManagedBuffer(raw_buffer, self)

    def _return_buffer(self, buffer: bytearray):
        """
        Internal method to return buffer to pool.

        Args:
            buffer: The buffer to return to the pool
        """
        if len(self._available) < self._max_pool_size:
            # Return buffer to pool without clearing (data is sliced appropriately)
            self._available.append(buffer)
        # If pool is full, let buffer be garbage collected


class ManagedBuffer:
    """
    A buffer wrapper that automatically returns to pool when dereferenced.

    Provides a view() method for creating memoryview slices and implements
    the buffer protocol for direct tensor construction. The buffer is
    automatically returned to the pool when no more references exist.
    """

    def __init__(self, buffer: bytearray, pool: BufferPool):
        """
        Create a managed buffer.

        Args:
            buffer: The underlying buffer to manage
            pool: The pool this buffer belongs to
        """
        self._buffer = buffer
        self._pool = pool
        self._active = True

        # Automatically return to pool when garbage collected
        weakref.finalize(self, pool._return_buffer, buffer)

    def view(self, start: int = 0, end: Optional[int] = None) -> memoryview:
        """
        Get memoryview slice - supports numpy/torch frombuffer directly.

        Args:
            start: Start index (inclusive)
            end: End index (exclusive), or None for end of buffer

        Returns:
            memoryview: View into the buffer slice

        Raises:
            RuntimeError: If buffer has been returned to pool
        """
        if not self._active:
            raise RuntimeError("Buffer has been returned to pool")

        if end is None:
            end = len(self._buffer)
        return memoryview(self._buffer)[start:end]

    def __repr__(self):
        if not self._active:
            return "ManagedBuffer(returned to pool)"
        return f"ManagedBuffer({len(self._buffer)} bytes)"

    # Buffer protocol support for numpy/torch
    def __buffer__(self, flags):
        """Support buffer protocol"""
        if not self._active:
            raise RuntimeError("Buffer has been returned to pool")
        return self._buffer.__buffer__(flags)

    def __release_buffer__(self, view):
        """Release buffer protocol view."""
        if hasattr(self._buffer, '__release_buffer__'):
            self._buffer.__release_buffer__(view)


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