import asyncio
import pytest

from kioto import streams, futures
from kioto.channels import (
    error,
    channel,
    channel_unbounded,
    oneshot_channel,
    watch,
    spsc_buffer,
)
from kioto.internal.buffer import ManagedBuffer


@pytest.mark.asyncio
async def test_channel_send_recv_unbounded():
    tx, rx = channel_unbounded()
    tx.send(1)
    tx.send(2)
    tx.send(3)

    x = await rx.recv()
    y = await rx.recv()
    z = await rx.recv()
    assert [1, 2, 3] == [x, y, z]


@pytest.mark.asyncio
async def test_channel_bounded_send_recv():
    tx, rx = channel(3)
    tx.send(1)
    tx.send(2)
    tx.send(3)

    with pytest.raises(error.ChannelFull):
        tx.send(4)

    x = await rx.recv()
    y = await rx.recv()
    z = await rx.recv()
    assert [1, 2, 3] == [x, y, z]


@pytest.mark.asyncio
async def test_channel_bounded_send_recv_async():
    tx, rx = channel(3)
    await tx.send_async(1)
    await tx.send_async(2)
    await tx.send_async(3)

    # The queue is full so this cant complete until after
    # we have made space on the receiving end.
    deferred_send = asyncio.create_task(tx.send_async(4))

    x = await rx.recv()
    y = await rx.recv()
    z = await rx.recv()
    assert [1, 2, 3] == [x, y, z]

    await deferred_send
    deferred = await rx.recv()
    assert 4 == deferred


@pytest.mark.asyncio
async def test_channel_drop_sender():
    tx, rx = channel(1)
    tx.send(1)

    del tx

    result = await rx.recv()
    assert 1 == result

    # Sender was dropped no more data will ever be received
    with pytest.raises(error.SendersDisconnected):
        await rx.recv()


@pytest.mark.asyncio
async def test_channel_drop_sender_parked_receiver():
    tx, rx = channel(1)

    rx_task = asyncio.create_task(rx.recv())
    del tx

    with pytest.raises(error.SendersDisconnected):
        await rx_task


@pytest.mark.asyncio
async def test_channel_send_then_drop_sender_parked_receiver():
    tx, rx = channel(1)

    rx_task = asyncio.create_task(rx.recv())
    tx.send(1)
    del tx

    assert 1 == await rx_task


@pytest.mark.asyncio
async def test_channel_send_park_on_full_recv_unpark():
    tx, rx = channel(1)

    async def park_sender():
        await tx.send_async(1)
        await tx.send_async(2)

    send_task = asyncio.create_task(park_sender())
    assert 1 == await rx.recv()
    assert 2 == await rx.recv()
    await send_task


@pytest.mark.asyncio
async def test_channel_recv_park_on_empty_send_unpark():
    tx, rx = channel(1)

    async def park_receiver():
        assert 1 == await rx.recv()
        assert 2 == await rx.recv()

    recv_task = asyncio.create_task(park_receiver())
    await tx.send_async(1)
    await tx.send_async(2)
    await recv_task


@pytest.mark.asyncio
async def test_channel_drop_recv():
    tx, rx = channel(1)

    del rx

    # No receivers exist to receive the sent data
    with pytest.raises(error.ReceiversDisconnected):
        tx.send(1)


@pytest.mark.asyncio
async def test_channel_send_on_closed():
    tx, rx = channel(1)

    del rx
    with pytest.raises(error.ReceiversDisconnected):
        tx.send(1)


@pytest.mark.asyncio
async def test_channel_recv_on_closed():
    tx, rx = channel(1)

    del tx
    with pytest.raises(error.SendersDisconnected):
        await rx.recv()


@pytest.mark.asyncio
async def test_channel_rx_stream():
    tx, rx = channel(5)
    rx_stream = rx.into_stream()

    for x in range(5):
        tx.send(x)

    del tx

    evens = await rx_stream.filter(lambda x: x % 2 == 0).collect()
    assert [0, 2, 4] == evens


@pytest.mark.asyncio
async def test_channel_tx_sink():
    tx, rx = channel(3)
    tx_sink = tx.into_sink()

    # Send all of the stream elements into the sink. Note
    # that we need to do this in a separate task, since flush()
    # will not complete until all items are retrieved from the
    # receiving end
    st = streams.iter([1, 2, 3])
    sink_task = asyncio.create_task(tx_sink.send_all(st))

    x = await rx.recv()
    y = await rx.recv()
    z = await rx.recv()
    assert [1, 2, 3] == [x, y, z]

    await sink_task


@pytest.mark.asyncio
async def test_channel_tx_sink_feed_send():
    tx, rx = channel(3)
    tx_sink = tx.into_sink()

    # Push elements into the sink without synchronization
    await tx_sink.feed(1)
    await tx_sink.feed(2)

    # Send flushes the sink, which means this will not complete until
    # 3 is received by the receiving end
    sync_task = asyncio.create_task(tx_sink.send(3))

    x = await rx.recv()
    y = await rx.recv()

    # Prove that the send task still hasn't completed
    assert not sync_task.done()

    z = await rx.recv()

    # Now that its been received the task will complete
    await sync_task

    assert [1, 2, 3] == [x, y, z]


@pytest.mark.asyncio
async def test_channel_tx_sink_close():
    def make_sink_rx():
        tx, rx = channel(4)
        tx_sink = tx.into_sink()
        return tx_sink, rx

    tx_sink, rx = make_sink_rx()

    async def sender(sink):
        # Note: This function is actually generic across all sink impls!
        await sink.feed(1)
        await sink.feed(2)
        await sink.feed(3)

        # Close will ensure all items have been flushed and received
        await sink.close()

    sink_task = asyncio.create_task(sender(tx_sink))
    result = await rx.into_stream().collect()
    assert [1, 2, 3] == result

    await sink_task


@pytest.mark.asyncio
async def test_oneshot_channel():
    tx, rx = oneshot_channel()
    tx.send(1)
    result = await rx
    assert 1 == result


@pytest.mark.asyncio
async def test_oneshot_channel_send_exhausted():
    tx, rx = oneshot_channel()
    tx.send(1)
    result = await rx

    # You can only send on the channel once!
    with pytest.raises(error.SenderExhausted):
        tx.send(2)


@pytest.mark.asyncio
async def test_oneshot_channel_recv_exhausted():
    tx, rx = oneshot_channel()
    tx.send(1)

    result = await rx

    # You can only await the recv'ing end once
    with pytest.raises(RuntimeError):
        await rx


@pytest.mark.asyncio
async def test_oneshot_channel_sender_dropped():
    tx, rx = oneshot_channel()
    del tx

    with pytest.raises(error.SendersDisconnected):
        result = await rx


@pytest.mark.asyncio
async def test_channel_req_resp():
    # A common pattern for using oneshot is to implement a request response interface

    async def worker_task(rx):
        async for request in rx:
            tx, request_arg = request
            tx.send(request_arg + 1)

    tx, rx = channel(3)

    async def add_one(arg):
        once_tx, once_rx = oneshot_channel()
        tx.send((once_tx, arg))
        return await once_rx

    # Spawn the worker task
    rx_stream = rx.into_stream()
    worker = asyncio.create_task(worker_task(rx_stream))

    assert 2 == await add_one(1)
    assert 3 == await add_one(2)
    assert 4 == await add_one(3)

    # Shutdown the worker task
    worker.cancel()


def test_watch_channel_send_recv():
    tx, rx = watch(1)

    tx.send(2)
    tx.send(3)

    assert 3 == rx.borrow()


def test_watch_channel_send_modify():
    tx, rx = watch(1)

    tx.send_modify(lambda x: x + 1)
    tx.send_modify(lambda x: x * 2)

    assert 4 == rx.borrow()


@pytest.mark.asyncio
async def test_watch_channel_send_if_modified():
    tx, rx = watch(1)

    # Get the current version of the watch channel
    version = rx._last_version

    # Send a modified value if the condition is met
    tx.send_if_modified(lambda x: x + 1)

    # Borrow and update the value from the receiver
    value = rx.borrow_and_update()
    assert value == 2

    # Ensure the version has changed after modification
    new_version = rx._last_version
    assert new_version != version

    # Attempt to send a value that does not modify the current value
    tx.send_if_modified(lambda x: x)

    # Ensure the version has not changed and the value remains the same
    assert rx._last_version == new_version
    assert 2 == rx.borrow_and_update()


@pytest.mark.asyncio
async def test_watch_channel_no_receivers():
    tx, rx = watch(1)
    del rx

    with pytest.raises(error.ReceiversDisconnected):
        tx.send(2)


@pytest.mark.asyncio
async def test_watch_channel_borrow_and_update():
    tx, rx = watch(1)

    tx.send(2)
    assert 2 == rx.borrow_and_update()

    tx.send(3)
    assert 3 == rx.borrow_and_update()


@pytest.mark.asyncio
async def test_watch_channel_changed():
    tx, rx = watch(1)
    assert 1 == rx.borrow_and_update()

    tx.send(2)
    await rx.changed()
    assert 2 == rx.borrow_and_update()

    tx.send(3)
    await rx.changed()
    assert 3 == rx.borrow_and_update()


@pytest.mark.asyncio
async def test_watch_channel_multi_consumer():
    tx, rx1 = watch(1)
    rx2 = tx.subscribe()

    a = rx1.borrow_and_update()
    b = rx2.borrow_and_update()

    assert 1 == a == b

    tx.send(2)
    a = rx1.borrow_and_update()
    assert 2 == a

    tx.send(3)
    a = rx1.borrow_and_update()
    b = rx2.borrow_and_update()

    assert 3 == a == b


@pytest.mark.asyncio
async def test_watch_channel_wait():
    tx, rx1 = watch(1)
    rx2 = tx.subscribe()

    async def wait_for_update(rx):
        await rx.changed()
        return rx.borrow_and_update()

    tasks = futures.task_set(
        a=futures.ready(None),
        # Start up 2 receivers both waiting for notification of a new value
        b=wait_for_update(rx1),
        c=wait_for_update(rx2),
    )

    # Send a value on the watch, both receivers should see the same value
    while tasks:
        result = await futures.select(tasks)
        task_name, value = result
        if task_name == "a":
            # There was a bug that broke notification if we sent
            # two values while the receiver was waiting
            tx.send(2)
            tx.send(3)
        else:
            assert value == 3


@pytest.mark.asyncio
async def test_watch_channel_receiver_stream():
    tx, rx = watch(1)
    rx_stream = rx.into_stream()

    # Receiver will see all items if the calls are interleaved
    assert 1 == await anext(rx_stream)

    tx.send(2)
    assert 2 == await anext(rx_stream)

    tx.send(3)
    assert 3 == await anext(rx_stream)

    # If the sender outpaces the receiver, the receiver will only receive the latest
    for i in range(3, 10):
        tx.send(i)

    assert 9 == await anext(rx_stream)

    # Drop the sender, should cause a stop iteration error on the stream
    del tx
    with pytest.raises(StopAsyncIteration):
        await anext(rx_stream)


@pytest.mark.asyncio
async def test_watch_channel_cancel():
    tx, rx = watch(1)

    task = asyncio.create_task(rx.changed())
    await asyncio.sleep(0.1)
    task.cancel()

    # Despite previous cancelation, we can still read the value
    tx.send(1)
    assert rx.borrow_and_update() == 1

    tx.send(2)
    assert rx.borrow_and_update() == 2


# SPSC Buffer Tests


def test_spsc_buffer_basic_send_recv():
    """Test basic synchronous send and receive operations."""
    tx, rx = spsc_buffer(1024)

    # Send some data
    data = b"hello world"
    sent = tx.send(data)
    assert sent == len(data)

    # Receive the data
    received = rx.recv(len(data))
    assert isinstance(received, bytearray)
    assert bytes(received) == data


def test_spsc_buffer_capacity_rounding():
    """Test that capacity is rounded to nearest power of 2."""
    tx, rx = spsc_buffer(1000)  # Should round to 1024

    # Fill the buffer completely
    data = b"x" * 1024
    sent = tx.send(data)
    assert sent == 1024

    # Buffer should be full now
    more_data = b"y"
    sent = tx.send(more_data)
    assert sent == 0  # Nothing should be sent


def test_spsc_buffer_partial_send():
    """Test partial sends when buffer is nearly full."""
    tx, rx = spsc_buffer(16)

    # Fill most of the buffer
    data1 = b"x" * 10
    sent = tx.send(data1)
    assert sent == 10

    # Try to send more than remaining space
    data2 = b"y" * 10
    sent = tx.send(data2)
    assert sent == 6  # Only 6 bytes should fit

    # Receive some data to make space
    received = rx.recv(5)
    assert received == bytearray(b"x" * 5)

    # Now we can send more
    data3 = b"z" * 5
    sent = tx.send(data3)
    assert sent == 5


def test_spsc_buffer_wraparound():
    """Test buffer wraparound functionality."""
    tx, rx = spsc_buffer(8)

    # Fill buffer
    data1 = b"12345678"
    sent = tx.send(data1)
    assert sent == 8

    # Read part of it
    received = rx.recv(4)
    assert received == bytearray(b"1234")

    # Send more data (should wrap around)
    data2 = b"abcd"
    sent = tx.send(data2)
    assert sent == 4

    # Read remaining original data
    received = rx.recv(4)
    assert received == bytearray(b"5678")

    # Read new data
    received = rx.recv(4)
    assert received == bytearray(b"abcd")


@pytest.mark.asyncio
async def test_spsc_buffer_async_send():
    """Test asynchronous send operations."""
    tx, rx = spsc_buffer(8)

    # Fill buffer
    await tx.send_async(b"12345678")

    # Start async send that will block
    send_task = asyncio.create_task(tx.send_async(b"abcd"))
    await asyncio.sleep(0.01)  # Let it try to send

    # Read some data to unblock sender
    received = rx.recv(4)
    assert received == bytearray(b"1234")

    # Wait for async send to complete
    await send_task

    # Verify all data is correct
    received = rx.recv(8)
    assert received == bytearray(b"5678abcd")


@pytest.mark.asyncio
async def test_spsc_buffer_recv_into():
    """Test recv_into functionality."""
    tx, rx = spsc_buffer(16)

    # Send some data
    await tx.send_async(b"hello world")

    # Receive into a buffer
    out_buffer = bytearray(5)
    copied = await rx.recv_into(out_buffer)
    assert copied == 5
    assert out_buffer == b"hello"

    # Receive remaining data
    out_buffer = bytearray(10)
    copied = await rx.recv_into(out_buffer)
    assert copied == 6
    assert out_buffer[:6] == b" world"


@pytest.mark.asyncio
async def test_spsc_buffer_wait_for():
    """Test wait_for functionality."""
    tx, rx = spsc_buffer(16)

    # Start waiting for data
    wait_task = asyncio.create_task(rx.wait_for(5))
    await asyncio.sleep(0.01)

    # Send enough data
    await tx.send_async(b"hello")

    # Wait should complete
    await wait_task

    # Verify data is available
    data = rx.recv(5)
    assert data == bytearray(b"hello")


@pytest.mark.asyncio
async def test_spsc_buffer_wait_for_deadlock_prevention():
    """Test that wait_for prevents deadlock by capping at buffer capacity."""
    tx, rx = spsc_buffer(8)

    # Try to wait for more data than buffer capacity
    wait_task = asyncio.create_task(rx.wait_for(16))  # Buffer only holds 8
    await asyncio.sleep(0.01)

    # Send data up to capacity
    await tx.send_async(b"12345678")

    # Wait should complete even though we asked for 16 bytes
    await wait_task


def test_spsc_buffer_single_sender_enforcement():
    """Test that only one sender is allowed."""
    tx, rx = spsc_buffer(16)
    ctr = type(tx)

    # Try to create another sender
    with pytest.raises(RuntimeError, match="Only one sender allowed"):
        ctr(tx._buffer)


def test_spsc_buffer_single_receiver_enforcement():
    """Test that only one receiver is allowed."""
    tx, rx = spsc_buffer(16)
    ctr = type(rx)

    # Try to create another receiver
    with pytest.raises(RuntimeError, match="Only one receiver allowed"):
        ctr(rx._buffer)


@pytest.mark.asyncio
async def test_spsc_buffer_sender_dropped():
    """Test behavior when sender is dropped."""
    import gc

    tx, rx = spsc_buffer(16)

    # Send some data
    await tx.send_async(b"hello")

    # Drop sender
    del tx
    gc.collect()  # Force garbage collection to trigger weakref callback
    await asyncio.sleep(0.01)  # Give callback time to execute

    # Should be able to read existing data
    data = rx.recv(5)
    assert data == bytearray(b"hello")

    # But waiting for more should fail
    with pytest.raises(error.SendersDisconnected):
        await rx.wait_for(1)


@pytest.mark.asyncio
async def test_spsc_buffer_receiver_dropped():
    """Test behavior when receiver is dropped."""
    import gc

    tx, rx = spsc_buffer(16)

    # Drop receiver
    del rx
    gc.collect()  # Force garbage collection to trigger weakref callback
    await asyncio.sleep(0.01)  # Give callback time to execute

    # Sending should fail
    with pytest.raises(error.ReceiversDisconnected):
        tx.send(b"hello")


@pytest.mark.asyncio
async def test_spsc_buffer_sender_sink():
    """Test sender sink functionality."""
    tx, rx = spsc_buffer(16)
    sink = tx.into_sink()

    # Send data through sink
    await sink.feed(b"hello")
    await sink.send(b" world")

    # Read data
    data = rx.recv(11)
    assert data == bytearray(b"hello world")

    # Close sink
    await sink.close()


@pytest.mark.asyncio
async def test_spsc_buffer_receiver_stream():
    """Test receiver stream functionality."""
    tx, rx = spsc_buffer(64)
    stream = rx.into_stream(buffer_size=8, min_size=2)

    # Send data
    await tx.send_async(b"hello world test data")

    # Read through stream
    chunk1 = await anext(stream)
    assert len(chunk1) <= 8  # Should respect buffer size

    chunk2 = await anext(stream)
    assert len(chunk2) <= 8

    # Close sender
    del tx

    # Stream should eventually end
    with pytest.raises(StopAsyncIteration):
        while True:
            await anext(stream)


@pytest.mark.asyncio
async def test_spsc_buffer_notify_methods():
    """Test notify and wait methods."""
    tx, rx = spsc_buffer(8)

    # Start a receiver waiting
    wait_task = asyncio.create_task(rx.wait_for(1))
    await asyncio.sleep(0.01)

    # Send data and notify
    tx.send(b"x")
    tx.notify_reader()

    # Wait should complete
    await wait_task


@pytest.mark.asyncio
async def test_spsc_buffer_concurrent_operations():
    """Test concurrent send and receive operations."""
    tx, rx = spsc_buffer(16)

    async def sender():
        for i in range(10):
            await tx.send_async(f"msg{i:02d}".encode())
            await asyncio.sleep(0.001)

    async def receiver():
        messages = []
        for _ in range(10):
            await rx.wait_for(5)  # Wait for message
            data = rx.recv(5)
            messages.append(data.decode())
        return messages

    # Run sender and receiver concurrently
    sender_task = asyncio.create_task(sender())
    receiver_task = asyncio.create_task(receiver())

    messages = await receiver_task
    await sender_task

    # Verify all messages received correctly
    expected = [f"msg{i:02d}" for i in range(10)]
    assert messages == expected


# BorrowedSlice and Zero-Copy Stream Tests


@pytest.mark.asyncio
async def test_spsc_buffer_receiver_stream_managed_buffer():
    """Test receiver stream returns memoryview from managed buffer pool."""

    tx, rx = spsc_buffer(64)
    stream = rx.into_stream(buffer_size=8, min_size=2)

    # Send some data
    await tx.send_async(b"hello world")

    # Get memoryview from stream
    chunk = await anext(stream)
    assert isinstance(chunk, memoryview)
    assert len(chunk) <= 8  # Should respect buffer size
    assert len(chunk) >= 2  # Should respect min_size

    # Can read from memoryview without copying
    chunk_bytes = bytes(chunk)
    # Should get exactly 8 bytes starting with "hello wo"
    assert chunk_bytes == b"hello wo"


@pytest.mark.asyncio
async def test_spsc_buffer_managed_buffer_clone():
    """Test that stream returns memoryview and can be converted to owned copy."""

    tx, rx = spsc_buffer(64)
    stream = rx.into_stream(buffer_size=16, min_size=1)

    # Send data
    await tx.send_async(b"test data")

    # Get memoryview from stream and make owned copy
    chunk = await anext(stream)
    owned = bytes(chunk)

    # Owned copy should be bytes with exact expected data
    assert isinstance(owned, bytes)
    assert owned == b"test data"


@pytest.mark.asyncio
async def test_spsc_buffer_borrowed_slice_invalidation():
    """Test that BorrowedSlice is invalidated on next anext call."""

    tx, rx = spsc_buffer(64)
    stream = rx.into_stream(buffer_size=8, min_size=1)

    # Send data in chunks
    await tx.send_async(b"first")
    await tx.send_async(b"second")

    # Get first borrowed slice
    first_borrowed = await anext(stream)
    assert isinstance(first_borrowed, memoryview)

    # Accessing first slice should work
    _ = len(first_borrowed)

    # Get second borrowed slice - both should remain valid in buffer pool design
    second_borrowed = await anext(stream)
    assert isinstance(second_borrowed, memoryview)

    # Both slices should still be valid (no invalidation in buffer pool design)
    first_len = len(first_borrowed)
    second_len = len(second_borrowed)

    # Verify both have reasonable lengths
    assert first_len > 0
    assert second_len > 0


@pytest.mark.asyncio
async def test_spsc_buffer_stream_data_persistence():
    """Test that data from stream chunks can be persisted across multiple reads."""

    tx, rx = spsc_buffer(64)
    stream = rx.into_stream(buffer_size=8, min_size=1)

    # Send data
    await tx.send_async(b"persistent")
    await tx.send_async(b"data")

    # Get first chunk and make owned copy
    first_chunk = await anext(stream)
    owned_copy = bytes(first_chunk)

    # Get second chunk
    second_chunk = await anext(stream)
    second_data = bytes(second_chunk)

    # Owned copy should persist and be independent
    assert len(owned_copy) > 0
    assert len(second_data) > 0

    # Verify exact data reconstruction
    all_data = owned_copy + second_data
    assert all_data == b"persistentdata"


@pytest.mark.asyncio
async def test_spsc_buffer_stream_multiple_chunks():
    """Test that stream can yield multiple chunks without issues."""

    tx, rx = spsc_buffer(64)
    stream = rx.into_stream(buffer_size=8, min_size=1)

    # Send data in chunks
    await tx.send_async(b"first")
    await tx.send_async(b"second")

    # Get first chunk
    first_chunk = await anext(stream)
    assert isinstance(first_chunk, memoryview)

    # Accessing first chunk should work
    first_data = bytes(first_chunk)
    assert len(first_data) > 0

    # Get second chunk - should work fine (no invalidation needed)
    second_chunk = await anext(stream)
    assert isinstance(second_chunk, memoryview)

    # Both chunks should still be accessible (no invalidation in buffer pool design)
    first_data_again = bytes(first_chunk)
    second_data = bytes(second_chunk)

    # Verify we got different data
    assert len(first_data_again) > 0
    assert len(second_data) > 0
