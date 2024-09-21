import asyncio
import pytest

from kioto import streams
from kioto.channels import channel, channel_unbounded, oneshot_channel

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

    with pytest.raises(asyncio.QueueFull):
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

    # This should be called when the sender is garbage collected
    tx._close()

    result = await rx.recv()
    assert 1 == result

    # Sender was dropped no more data will ever be received
    with pytest.raises(RuntimeError):
        await rx.recv()

@pytest.mark.asyncio
async def test_channel_drop_recv():
    tx, rx = channel(1)

    # This should be called when the receiver is garbage collected
    rx._close()

    # No receivers exist to receive the sent data
    with pytest.raises(RuntimeError):
        tx.send(1)

@pytest.mark.asyncio
async def test_channel_send_on_closed():
    tx, rx = channel(1)

    tx._close()
    with pytest.raises(RuntimeError):
        tx.send(1)

@pytest.mark.asyncio
async def test_channel_recv_on_closed():
    tx, rx = channel(1)

    rx._close()
    with pytest.raises(RuntimeError):
        await rx.recv()


@pytest.mark.asyncio
async def test_channel_rx_stream():
    tx, rx = channel(5)
    rx_stream = rx.into_stream()

    for x in range(5):
        tx.send(x)

    # This should be called when the receiver is garbage collected
    tx._close()

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
    tx, rx = channel(3)
    tx_sink = tx.into_sink()

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
    with pytest.raises(RuntimeError):
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