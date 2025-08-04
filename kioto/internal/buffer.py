"""
Buffer pool module for efficient memory management with automatic lifecycle.

Provides BufferPool and ManagedBuffer classes that enable zero-copy operations
while automatically managing buffer lifetime through garbage collection.
"""

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