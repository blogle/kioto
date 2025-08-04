import pytest
import weakref
import gc
from kioto.internal.buffer import BufferPool, ManagedBuffer


def test_buffer_pool_creation():
    """Test BufferPool creation with different sizes."""
    pool = BufferPool(1024, 5)
    assert pool._buffer_size == 1024
    assert pool._max_pool_size == 5
    assert len(pool._available) == 0


def test_managed_buffer_basic_operations():
    """Test basic ManagedBuffer operations."""
    pool = BufferPool(16, 2)
    buffer = pool.get_buffer()

    # Test basic properties
    assert isinstance(buffer, ManagedBuffer)
    assert len(buffer._buffer) == 16  # Access underlying buffer directly
    assert repr(buffer) == "ManagedBuffer(16 bytes)"


def test_managed_buffer_view():
    """Test ManagedBuffer.view() method."""
    pool = BufferPool(16, 2)
    buffer = pool.get_buffer()

    # Fill with test data
    test_data = b"hello world"
    buffer._buffer[:len(test_data)] = test_data

    # Test full view
    view = buffer.view()
    assert isinstance(view, memoryview)
    assert len(view) == 16

    # Test partial view
    partial_view = buffer.view(0, 5)
    assert bytes(partial_view) == b"hello"

    # Test start-only view
    start_view = buffer.view(6)
    assert bytes(start_view) == b"world" + b"\x00" * 5


def test_buffer_pool_reuse():
    """Test that buffers are properly reused from pool."""
    pool = BufferPool(16, 2)

    # Get buffer and modify it
    buffer1 = pool.get_buffer()
    buffer1._buffer[0] = ord('x')

    # Get ID for later comparison
    buffer1_id = id(buffer1._buffer)

    # Release reference and force GC
    del buffer1
    gc.collect()

    # Get new buffer - should reuse the same underlying buffer
    buffer2 = pool.get_buffer()
    buffer2_id = id(buffer2._buffer)

    # Should be the same underlying buffer (pool reuse)
    assert buffer1_id == buffer2_id


def test_buffer_pool_max_size():
    """Test that pool respects max size limit."""
    pool = BufferPool(16, 2)

    # Get and release more buffers than max pool size
    buffers = []
    for i in range(5):
        buf = pool.get_buffer()
        buf._buffer[0] = i  # Mark the buffer
        buffers.append(id(buf._buffer))

    # Release all buffers
    del buffers
    gc.collect()

    # Pool should only have max_pool_size buffers
    assert len(pool._available) <= 2


def test_managed_buffer_buffer_protocol():
    """Test that ManagedBuffer implements buffer protocol."""
    pool = BufferPool(16, 2)
    buffer = pool.get_buffer()

    # Fill with test data
    test_data = b"hello"
    buffer._buffer[:len(test_data)] = test_data

    # Test buffer protocol via memoryview construction
    mv = memoryview(buffer)
    assert bytes(mv[:5]) == b"hello"


def test_managed_buffer_automatic_return():
    """Test that buffers are automatically returned to pool."""
    pool = BufferPool(16, 2)

    # Pool starts empty
    assert len(pool._available) == 0

    # Create and destroy buffer
    buffer = pool.get_buffer()
    del buffer
    gc.collect()

    # Buffer should be returned to pool
    assert len(pool._available) == 1


def test_managed_buffer_error_after_return():
    """Test that accessing returned buffer raises error."""
    pool = BufferPool(16, 2)
    buffer = pool.get_buffer()

    # Manually mark as inactive (simulating return to pool)
    buffer._active = False

    # Should raise errors
    with pytest.raises(RuntimeError, match="returned to pool"):
        buffer.view()


def test_empty_buffer_handling():
    """Test handling of empty buffers."""
    pool = BufferPool(16, 2)

    # Test empty view
    buffer = pool.get_buffer()
    empty_view = buffer.view(0, 0)
    assert len(empty_view) == 0
    assert isinstance(empty_view, memoryview)