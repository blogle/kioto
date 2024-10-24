import asyncio
import pytest

from kioto import streams, futures
from kioto.channels import channel, channel_unbounded, oneshot_channel, watch

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

    del tx

    result = await rx.recv()
    assert 1 == result

    # Sender was dropped no more data will ever be received
    with pytest.raises(RuntimeError):
        await rx.recv()

@pytest.mark.asyncio
async def test_channel_drop_recv():
    tx, rx = channel(1)

    del rx

    # No receivers exist to receive the sent data
    with pytest.raises(RuntimeError):
        tx.send(1)

@pytest.mark.asyncio
async def test_channel_send_on_closed():
    tx, rx = channel(1)

    del rx
    with pytest.raises(RuntimeError):
        tx.send(1)

@pytest.mark.asyncio
async def test_channel_recv_on_closed():
    tx, rx = channel(1)

    del tx
    with pytest.raises(RuntimeError):
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
async def test_oneshot_channel_sender_dropped():
    tx, rx = oneshot_channel()
    del tx

    with pytest.raises(RuntimeError):
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

    with pytest.raises(RuntimeError, match="No receivers exist. Cannot send."):
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
        c=wait_for_update(rx2)
    )

    # Send a value on the watch, both receivers should see the same value
    while tasks:

        match await futures.select(tasks):
            case ("a", _):
                tx.send(2)
            case (_, value):
                assert value == 2

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

