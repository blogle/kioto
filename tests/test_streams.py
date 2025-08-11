import asyncio
import pytest
import time

from kioto import streams
from kioto.channels import channel
from kioto.sink.impl import Sink


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
async def test_then():
    async def task(arg):
        return arg * 2

    iterable = [1, 2, 3, 4, 5]
    stream = streams.iter(iterable).then(task)

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
async def test_buffered_early_yield():
    @streams.async_stream
    async def slow_stream():
        yield 1
        yield 2
        yield 3
        await asyncio.sleep(1)
        yield 4

    async def f(x):
        await asyncio.sleep(0.1)
        return x

    # Here we are requesting more buffered space than the actual stream contains.
    # We want to assert that our stream yields elements before the sleep and before
    # we hit the StopAsyncIteration
    st = slow_stream().map(f).buffered(5)
    then = time.monotonic()
    assert 1 == await anext(st)
    assert 2 == await anext(st)
    assert 3 == await anext(st)
    duration = time.monotonic() - then

    # Check that we got concurrency
    assert duration < 0.15

    # Check that buffered wasn't blocked on the 4th element
    assert duration < 1

    assert 4 == await anext(st)
    with pytest.raises(StopAsyncIteration):
        await anext(st)


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
    possible = {0, 1, 2, 3, 4}
    assert value in possible

    # Ensure iteration (within the collect) works
    remaining = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9} - {value}
    assert set(await stream.collect()) == remaining

    # Ensure that the stream was executed concurrently
    duration = time.monotonic() - now
    assert duration < 1.2 * (n // c)


@pytest.mark.asyncio
async def test_buffered_unordered_early_yield():
    @streams.async_stream
    async def slow_stream():
        yield 1
        yield 2
        yield 3
        await asyncio.sleep(1)
        yield 4

    async def f(x):
        await asyncio.sleep(0.1)
        return x

    # Here we are requesting more buffered space than the actual stream contains.
    # We want to assert that our stream yields elements before the sleep and before
    # we hit the StopAsyncIteration
    st = slow_stream().map(f).buffered_unordered(5)
    then = time.monotonic()
    a = await anext(st)
    b = await anext(st)
    c = await anext(st)
    duration = time.monotonic() - then

    assert {a, b, c} == {1, 2, 3}

    # Check that we got concurrency
    assert duration < 0.15

    # Check that buffered wasn't blocked on the 4th element
    assert duration < 1

    assert 4 == await anext(st)
    with pytest.raises(StopAsyncIteration):
        await anext(st)


class TaskCounter:
    def __init__(self):
        self.current_running = 0
        self.max_running = 0
        self.lock = asyncio.Lock()

    async def process(self, x):
        async with self.lock:
            self.current_running += 1
            self.max_running = max(self.current_running, self.max_running)

        # Simulate work
        await asyncio.sleep(0.1)

        async with self.lock:
            self.current_running -= 1

        return x


@pytest.mark.asyncio
@pytest.mark.parametrize("n", [1, 5, 10, 11])
async def test_buffered_concurrency_level(n):
    """
    Test that a buffered stream with a buffer size of 1 never runs more than one task concurrently.
    """
    # Create a stream, map each item with 'process', then buffer with a capacity of 1.
    elems = 10
    tc = TaskCounter()
    results = await streams.iter(range(elems)).map(tc.process).buffered(n).collect()

    # Verify that all items were processed and that concurrency never exceeded 1.
    min_concur = max(n - 1, 1)
    assert results == list(range(elems))
    assert (
        min_concur <= tc.max_running
    ), f"Max concurrency was {tc.max_running}, expected at least {min_concur}"


@pytest.mark.asyncio
@pytest.mark.parametrize("n", [1, 5, 10, 11])
async def test_buffered_unordered_concurrency_level(n):
    """
    Test that a buffered-unordered stream with a buffer size of 1 never runs more than one task concurrently.
    """
    # Create a stream, map each item with 'process', then buffer with a capacity of 1.
    elems = 10
    tc = TaskCounter()
    results = (
        await streams.iter(range(elems)).map(tc.process).buffered_unordered(n).collect()
    )

    # Verify that all items were processed and that concurrency never exceeded 1.
    concur = min(elems, n)
    assert set(results) == set(range(elems))
    assert (
        tc.max_running == concur
    ), f"Max concurrency was {tc.max_running}, expected == {n}"


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
        for i in range(n - 1):
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
    stream = streams.iter(range(3)).chain(streams.iter(range(3, 6)))

    # Ensure anext works
    assert await anext(stream) == 0

    # Ensure iteration (within the collect) works
    result = await stream.collect()
    assert result == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_zip():
    stream = streams.iter(range(3)).zip(streams.iter(range(3, 6)))

    # Ensure anext works
    assert await anext(stream) == (0, 3)

    # Ensure iteration (within the collect) works
    result = await stream.collect()
    assert result == [(1, 4), (2, 5)]


@pytest.mark.asyncio
async def test_switch():
    async def f(n):
        await asyncio.sleep(1)
        return n

    now = time.monotonic()

    # Switch on a function that sleeps for a second an returns the input, the next
    # value is always available on the stream, so we should only get the last result.
    stream = streams.iter(range(4)).switch(f)
    result = await stream.collect()
    assert result == [3]

    # We should have only waited for one of the coroutines to complete
    duration = time.monotonic() - now
    assert duration < 1.1


@pytest.mark.asyncio
async def test_debounce():
    @streams.async_stream
    async def st():
        n = 5
        for i in range(n):
            await asyncio.sleep(0.05)
            yield i

        await asyncio.sleep(0.6)
        yield n

    # The initial batch of values each return within our debounce duration,
    # so we skip all but the last in the batch. Then after a longer delay
    # we exhaust the stream which will also be yielded.
    stream = st()
    result = await stream.debounce(0.500).collect()

    assert result == [4, 5]


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
        odds=streams.iter([1, 3, 5, 7]), evens=streams.iter([2, 4, 6])
    )

    def to_even(result):
        match result:
            case "odds", number:
                return number + 1
            case "evens", number:
                return number

    results = await select_stream.map(to_even).collect()
    assert set(results) == {2, 2, 4, 4, 6, 6, 8}


@pytest.mark.asyncio
async def test_stream_select_once():
    s = streams.select(one=streams.once(1), never=streams.pending())

    name, value = await anext(s)
    assert name == "one"
    assert value == 1

    # Pending task will never return
    with pytest.raises(asyncio.TimeoutError):
        name, value = await asyncio.wait_for(anext(s), timeout=0.1)
        # NOTE: There was a bug causing the previous task to be re-yielded
        assert name != "one"
        assert value != 1


@pytest.mark.asyncio
async def test_enumerate():
    stream = streams.iter(["a", "b"]).enumerate()
    assert await anext(stream) == (0, "a")
    assert await stream.collect() == [(1, "b")]


@pytest.mark.asyncio
async def test_unzip():
    left, right = await streams.iter([(1, "a"), (2, "b")]).unzip()
    assert left == [1, 2]
    assert right == ["a", "b"]


@pytest.mark.asyncio
async def test_count():
    assert await streams.iter(range(5)).count() == 5


@pytest.mark.asyncio
async def test_cycle():
    stream = streams.iter([1, 2]).cycle()
    assert [await anext(stream) for _ in range(4)] == [1, 2, 1, 2]


@pytest.mark.asyncio
async def test_any_all():
    assert await streams.iter(range(5)).any(lambda x: x == 3)
    assert await streams.iter(range(5)).all(lambda x: x < 5)
    assert not await streams.iter(range(5)).all(lambda x: x < 3)


@pytest.mark.asyncio
async def test_scan():
    stream = streams.iter(range(1, 4)).scan(0, lambda acc, val: acc + val)
    assert await stream.collect() == [1, 3, 6]


@pytest.mark.asyncio
async def test_skip_while():
    async def pred(x):
        await asyncio.sleep(0)
        return x < 3

    stream = streams.iter(range(5)).skip_while(pred)
    assert await anext(stream) == 3
    assert await stream.collect() == [4]


@pytest.mark.asyncio
async def test_take_while():
    async def pred(x):
        await asyncio.sleep(0)
        return x < 3

    stream = streams.iter(range(5)).take_while(pred)
    assert await anext(stream) == 0
    assert await stream.collect() == [1, 2]


@pytest.mark.asyncio
async def test_take_until():
    async def stopper():
        await asyncio.sleep(0.05)
        return "done"

    @streams.async_stream
    async def st():
        for i in range(10):
            await asyncio.sleep(0.01)
            yield i

    # Case 1: stopper interrupts the collect()
    stream = st().take_until(stopper())
    assert await stream.collect() == [0, 1, 2, 3]
    assert stream.take_result() == "done"

    # Future already resolved
    assert stream.take_future() is None

    # Iterate the rest of the stream
    assert await stream.collect() == [4, 5, 6, 7, 8, 9]

    # Case 2: remove the stopper to iterate uninterrupted
    stream = st().take_until(stopper())
    assert await anext(stream) == 0
    assert await anext(stream) == 1
    stopper = stream.take_future()
    assert await stream.collect() == [2, 3, 4, 5, 6, 7, 8, 9]
    assert await stopper == "done"


@pytest.mark.asyncio
async def test_take():
    stream = streams.iter(range(5)).take(3)
    assert await stream.collect() == [0, 1, 2]


@pytest.mark.asyncio
async def test_skip():
    stream = streams.iter(range(5)).skip(2)
    assert await anext(stream) == 2
    assert await stream.collect() == [3, 4]


class _ForwardSink(Sink):
    def __init__(self):
        self.items = []
        self.flushed = False
        self.closed = False

    async def feed(self, item):
        self.items.append(item)

    async def send(self, item):
        self.items.append(item)

    async def flush(self):
        self.flushed = True

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_forward():
    sink = _ForwardSink()
    await streams.iter([1, 2, 3]).forward(sink)
    assert sink.items == [1, 2, 3]
    assert sink.flushed
    assert sink.closed
