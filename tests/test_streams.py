import asyncio
import pytest
import time

from kioto import streams
from kioto.channels import channel

@pytest.mark.asyncio
async def test_iter():
    iterable = [1, 2, 3, 4, 5]
    stream = streams.iter(iterable)

    # Ensure anext works
    assert await anext(stream) == 1

    # Ensure iteration (within the collect) works
    result = await stream.collect()
    assert result == iterable[1:]


@pytest.mark.asyncio
async def test_map():
    iterable = [1, 2, 3, 4, 5]
    stream = streams.iter(iterable).map(lambda x: x * 2)

    # Ensure anext works
    assert await anext(stream) == 2

    # Ensure iteration (within the collect) works
    result = await stream.collect()
    assert result == [4, 6, 8, 10]


@pytest.mark.asyncio
async def test_filter():
    iterable = [1, 2, 3, 4, 5]
    stream = streams.iter(iterable).filter(lambda x: x % 2 == 0)
    # Ensure anext works
    assert await anext(stream) == 2

    # Ensure iteration (within the collect) works
    result = await stream.collect()
    assert result == [4]


@pytest.mark.asyncio
async def test_buffered():

    async def task(n):
        await asyncio.sleep(1)
        return n

    @streams.async_stream
    def stream_fut(n):
        for i in range(n):
            yield task(i)

    n, c = 10, 5
    now = time.monotonic()
    stream = stream_fut(n).buffered(c)

    # Ensure anext works
    assert await anext(stream) == 0

    # Ensure iteration (within the collect) works
    assert await stream.collect() == [1, 2, 3, 4, 5, 6, 7, 8, 9]

    # Ensure that the stream was executed concurrently
    duration = time.monotonic() - now
    assert duration < 1.2 * (n // c)


@pytest.mark.asyncio
async def test_buffered_unordered():

    async def task(n):
        await asyncio.sleep(1)
        return n

    @streams.async_stream
    def stream_fut(n):
        for i in range(n):
            yield task(i)

    n, c = 10, 5
    now = time.monotonic()
    stream = stream_fut(n).buffered_unordered(c)

    # Ensure anext works
    value = await anext(stream)
    possible = { 0, 1, 2, 3, 4 }
    assert value in possible

    # Ensure iteration (within the collect) works
    remaining = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9} - { value }
    assert set(await stream.collect()) == remaining

    # Ensure that the stream was executed concurrently
    duration = time.monotonic() - now
    assert duration < 1.2 * (n // c)


@pytest.mark.asyncio
async def test_unordered_optimization():
    @streams.async_stream
    def sleeps(n):
        for i in range(n):
            yield asyncio.sleep(1)

    async def expensive_task():
        await asyncio.sleep(5)

    @streams.async_stream
    def work_stream(n):
        # This will prevent the buffered stream from starting the next batch of tasks until it has completed.
        # Where the unordered version can keep starting tasks without waiting for this first one to complete.
        yield expensive_task()
        for i in range(n-1):
            yield asyncio.sleep(1)

    # If n is much larger than c, the unordered version should be faster
    # because it will not wait for the first c tasks to complete before
    # starting the next batch of c tasks
    n, c = 20, 5

    now = time.monotonic()
    s = await work_stream(n).buffered(c).collect()
    ordered_duration = time.monotonic() - now

    now = time.monotonic()
    s = await work_stream(n).buffered_unordered(c).collect()
    unordered_duration = time.monotonic() - now

    assert unordered_duration < ordered_duration


@pytest.mark.asyncio
async def test_flatten():

    async def repeat(i):
        for _ in range(i):
            yield i

    @streams.async_stream
    def test_stream():
        for i in range(1, 4):
            yield repeat(i)

    stream = test_stream().flatten()
    # Ensure anext works
    assert await anext(stream) == 1

    # Ensure iteration (within the collect) works
    result = await stream.collect()
    assert result == [2, 2, 3, 3, 3]

@pytest.mark.asyncio
async def test_flat_map():

    async def repeat(i):
        for _ in range(i):
            yield i

    stream = streams.iter(range(1, 4)).flat_map(repeat)
    # Ensure anext works
    assert await anext(stream) == 1

    # Ensure iteration (within the collect) works
    result = await stream.collect()
    assert result == [2, 2, 3, 3, 3]

@pytest.mark.asyncio
async def test_chunks():

    stream = streams.iter(range(10)).chunks(3)

    # Ensure anext works
    assert await anext(stream) == [0, 1, 2]

    # Ensure iteration (within the collect) works
    result = await stream.collect()
    assert result == [[3, 4, 5], [6, 7, 8], [9]]

@pytest.mark.asyncio
async def test_ready_chunks():
    tx, rx = channel(10)
    stream = rx.into_stream().ready_chunks(3)

    tx.send(0)
    tx.send(1)
    tx.send(2)

    # Ensure anext works
    assert await anext(stream) == [0, 1, 2]

    tx.send(3)
    tx.send(4)

    # Ensure anext works - returning the available chunk
    assert await anext(stream) == [3, 4]

    for i in range(5, 10):
        tx.send(i)

    # Drop the sender - no more messages will be coming
    del tx

    # Ensure iteration (within the collect) works
    result = await stream.collect()
    assert result == [[5, 6, 7], [8, 9]]


@pytest.mark.asyncio
async def test_filter_map():

    def maybe_double(i):
        if i % 2 == 0:
            return i * 2

    stream = streams.iter(range(10)).filter_map(maybe_double)

    # Ensure anext works
    assert await anext(stream) == 0

    # Ensure iteration (within the collect) works
    result = await stream.collect()
    assert result == [4, 8, 12, 16]

@pytest.mark.asyncio
async def test_chain():

    stream = streams.iter(range(3)).chain(
        streams.iter(range(3, 6))
    )

    # Ensure anext works
    assert await anext(stream) == 0

    # Ensure iteration (within the collect) works
    result = await stream.collect()
    assert result == [1, 2, 3, 4, 5]

@pytest.mark.asyncio
async def test_zip():

    stream = streams.iter(range(3)).zip(
        streams.iter(range(3, 6))
    )

    # Ensure anext works
    assert await anext(stream) == (0, 3)

    # Ensure iteration (within the collect) works
    result = await stream.collect()
    assert result == [(1, 4), (2, 5)]

@pytest.mark.asyncio
async def test_fold():

    def add(acc, val):
        return acc + val

    # Fold returns a future, so we need to await it
    assert await streams.iter(range(5)).fold(add, 0) == 10

# test functions
def f(x):
    return x + 1

def g(x):
    return x * 2

# test predicates
def h(x):
    return x % 2 == 0

def j(x):
    return x % 3 == 0

@pytest.mark.asyncio
async def test_map_map():
    a = streams.iter(range(10)).map(f).map(g)
    b = streams.iter(range(10)).map(lambda x: g(f(x)))
    assert await a.collect() == await b.collect()

@pytest.mark.asyncio
async def test_filter_filter():
    a = streams.iter(range(10)).filter(h).filter(j)
    b = streams.iter(range(10)).filter(lambda x: h(x) and j(x))
    assert await a.collect() == await b.collect()

@pytest.mark.asyncio
async def test_filter_map():
    a = streams.iter(range(10)).filter(h).map(f)
    b = streams.iter(range(10)).filter_map(lambda x: f(x) if h(x) else None)
    assert await a.collect() == await b.collect()

@pytest.mark.asyncio
async def test_map_fold():
    a = streams.iter(range(10)).map(f).fold(lambda acc, val: acc + val, 0)
    b = streams.iter(range(10)).fold(lambda acc, val: acc + f(val), 0)
    assert await a == await b

@pytest.mark.asyncio
async def test_stream_select():

    select_stream = streams.select(
        odds=streams.iter([1, 3, 5, 7]),
        evens=streams.iter([2, 4, 6])
    )

    def to_even(result):
        match result:
            case "odds", number:
                return number + 1
            case "evens", number:
                return number

    results = await select_stream.map(to_even).collect()
    assert set(results) == {2, 2, 4, 4, 6, 6, 8}
