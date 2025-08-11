from __future__ import annotations

import asyncio
import builtins
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Iterable,
    Optional,
    TypeVar,
    Generic,
)

from kioto.futures import pending, select, task_set
from kioto.internal.queue import SlotQueue
from kioto.sink.impl import Sink


T = TypeVar("T")
U = TypeVar("U")
R = TypeVar("R")


class _Sentinel:
    """Special marker object used to signal the end of the stream."""

    def __repr__(self):
        return "<_Sentinel>"


class Stream(Generic[T]):
    def __aiter__(self) -> AsyncIterator[T]:
        return self

    @staticmethod
    def from_generator(gen: Iterable[T] | AsyncIterator[T]) -> "Stream[T]":
        return _GenStream(gen)

    def map(self, fn: Callable[[T], U]) -> "Stream[U]":
        return Map(self, fn)

    def then(self, coro: Callable[[T], Awaitable[U]]) -> "Stream[U]":
        return Then(self, coro)

    def filter(self, predicate: Callable[[T], bool]) -> "Stream[T]":
        return Filter(self, predicate)

    def buffered(self, n: int) -> "Stream[T]":
        return Buffered(self, n)

    def buffered_unordered(self, n: int) -> "Stream[T]":
        return BufferedUnordered(self, n)

    def flatten(self: "Stream[Stream[U]]") -> "Stream[U]":
        return Flatten(self)

    def flat_map(self, fn: Callable[[T], "Stream[U]"]) -> "Stream[U]":
        return FlatMap(self, fn)

    def chunks(self, n: int) -> "Stream[list[T]]":
        return Chunks(self, n)

    def ready_chunks(self, n: int) -> "Stream[list[T]]":
        return ReadyChunks(self, n)

    def filter_map(self, fn: Callable[[T], Optional[U]]) -> "Stream[U]":
        return FilterMap(self, fn)

    def chain(self, stream: "Stream[T]") -> "Stream[T]":
        return Chain(self, stream)

    def zip(self, stream: "Stream[U]") -> "Stream[tuple[T, U]]":
        return Zip(self, stream)

    def switch(self, coro: Callable[[T], Awaitable[U]]) -> "Stream[U]":
        return Switch(self, coro)

    def debounce(self, duration: float) -> "Stream[T]":
        return Debounce(self, duration)

    def enumerate(self) -> "Stream[tuple[int, T]]":
        return Enumerate(self)

    async def unzip(self: "Stream[tuple[T, U]]") -> tuple[list[T], list[U]]:
        left: list[T] = []
        right: list[U] = []
        async for l, r in self:  # type: ignore[misc]
            left.append(l)
            right.append(r)
        return left, right

    async def count(self) -> int:
        n = 0
        async for _ in self:
            n += 1
        return n

    def cycle(self) -> "Stream[T]":
        return Cycle(self)

    async def any(self, predicate: Callable[[T], bool]) -> bool:
        async for val in self:
            if predicate(val):
                return True
        return False

    async def all(self, predicate: Callable[[T], bool]) -> bool:
        async for val in self:
            if not predicate(val):
                return False
        return True

    def scan(self, acc: U, fn: Callable[[U, T], U]) -> "Stream[U]":
        return Scan(self, acc, fn)

    def skip_while(self, predicate: Callable[[T], Awaitable[bool]]) -> "Stream[T]":
        return SkipWhile(self, predicate)

    def take_while(self, predicate: Callable[[T], Awaitable[bool]]) -> "Stream[T]":
        return TakeWhile(self, predicate)

    def take_until(self, fut: Awaitable[R]) -> "TakeUntil[T, R]":
        return TakeUntil(self, fut)

    def take(self, n: int) -> "Stream[T]":
        return Take(self, n)

    def skip(self, n: int) -> "Stream[T]":
        return Skip(self, n)

    async def forward(self, sink: Sink) -> None:
        async for item in self:
            await sink.feed(item)
        await sink.flush()
        await sink.close()

    async def fold(self, fn: Callable[[U, T], U], acc: U) -> U:
        async for val in self:
            acc = fn(acc, val)
        return acc

    async def collect(self) -> list[T]:
        return [i async for i in builtins.aiter(self)]


class Iter(Stream[T]):
    def __init__(self, iterable: Iterable[T]):
        self.iterable = builtins.iter(iterable)

    async def __anext__(self) -> T:
        try:
            return next(self.iterable)
        except StopIteration:
            raise StopAsyncIteration


class Map(Stream[U]):
    def __init__(self, stream: Stream[T], fn: Callable[[T], U]):
        self.fn = fn
        self.stream = stream

    async def __anext__(self) -> U:
        return self.fn(await anext(self.stream))


class Then(Stream[U]):
    def __init__(self, stream: Stream[T], fn: Callable[[T], Awaitable[U]]):
        self.fn = fn
        self.stream = stream

    async def __anext__(self) -> U:
        arg = await anext(self.stream)
        return await self.fn(arg)


class Filter(Stream[T]):
    def __init__(self, stream: Stream[T], predicate: Callable[[T], bool]):
        self.predicate = predicate
        self.stream = stream

    async def __anext__(self) -> T:
        while True:
            val = await anext(self.stream)
            if self.predicate(val):
                return val


async def _buffered(stream, buffer_size: int):
    """
    Buffered stream implementation that spawns tasks from the input stream,
    buffering up to buffer_size tasks. It yields results as soon as they are available.

    The approach uses two concurrent "threads":
      - One that pushes new tasks into a bounded queue (the spawner).
      - One that consumes results from that queue (the consumer).

    When the underlying stream is exhausted, a sentinel is enqueued so that the consumer
    terminates once all completed task results have been yielded.

    Args:
        stream: An async iterable representing the source stream.
        buffer_size: Maximum number of concurrent tasks to buffer.

    Yields:
        Results from the tasks as they complete.
    """
    result_queue = SlotQueue(buffer_size)

    # Convert each element from the stream into a task and push into the result_queue.
    # When the stream is exhausted, enqueue a sentinel value.
    async def push_tasks():
        async for coro in stream:
            # Create a reservation in the queue - this is to prevent
            # us from spawning a task without having space in the queue.
            async with result_queue.put() as slot:
                slot.value = asyncio.create_task(coro)

        async with result_queue.put() as slot:
            slot.value = _Sentinel()

    # Start a task that spawns tasks from the stream.
    spawner_task = asyncio.create_task(push_tasks())

    while True:
        async with result_queue.get() as slot:
            task = slot.value
            if isinstance(task, _Sentinel):
                break

            # Propagate the task result (or exception if the task failed)
            yield await task

    # Ensure the spawner task is awaited in case it is still running.
    await spawner_task


class Buffered(Stream[T]):
    """
    Buffered stream that spawns tasks from an underlying stream with a specified buffer size.

    Results are yielded as soon as individual tasks complete.
    """

    def __init__(self, stream: Stream[Awaitable[T]], buffer_size: int):
        self.stream = _buffered(stream, buffer_size)

    async def __anext__(self) -> T:
        return await anext(self.stream)


async def _buffered_unordered(stream, buffer_size: int):
    """
    Asynchronously buffers tasks from the given stream, allowing up to 'buffer_size'
    tasks to run concurrently, and yields their results in the order of completion.

    This implementation uses a task set to manage two types of tasks:
      - The "spawn" task that pulls the next element from the stream.
      - The buffered tasks (with names corresponding to slot IDs) that are running.

    If no available slot exists, a new task is deferred until a slot becomes free.

    Args:
        stream: An async iterable that yields tasks (or values to be wrapped in tasks).
        buffer_size: Maximum number of concurrent tasks.

    Yields:
        The result of each task as it completes.
    """
    # Start the task set with the first element of the stream under the "spawn" key.
    tasks = task_set(spawn=anext(stream))
    # Event to signal that at least one buffering slot is available.
    slot_notification = asyncio.Event()
    # Set of available slot IDs (represented as integers).
    available_slots = set(range(buffer_size))

    async def spawn_later(spawned_task):
        """
        Defers spawning of the task until a buffering slot becomes available.
        """
        await slot_notification.wait()
        return spawned_task

    while tasks:
        try:
            completion = await select(tasks)
        except StopAsyncIteration:
            # If the underlying stream is exhausted, continue processing remaining tasks.
            continue

        match completion:
            case ("spawn", spawned_task):
                try:
                    # Attempt to get an available slot.
                    slot_id = available_slots.pop()
                except KeyError:
                    # No slot available: clear the notification and defer the spawned task.
                    slot_notification.clear()
                    tasks.update("spawn", spawn_later(spawned_task))
                else:
                    # Assign the spawned task a unique slot name.
                    tasks.update(str(slot_id), spawned_task)
                    # Request the next task from the stream.
                    tasks.update("spawn", anext(stream))

            case (slot_name, result):
                # When a buffered task completes, free its slot.
                available_slots.add(int(slot_name))
                slot_notification.set()
                yield result


class BufferedUnordered(Stream[T]):
    """
    Stream implementation that yields results from tasks in an unordered fashion.

    It buffers tasks from the underlying stream up to 'buffer_size' concurrently.
    As soon as any task completes, its result is yielded and its slot is freed for reuse.
    """

    def __init__(self, stream: Stream[Awaitable[T]], buffer_size: int):
        self.stream = _buffered_unordered(stream, buffer_size)

    async def __anext__(self) -> T:
        return await anext(self.stream)


async def _flatten(nested_st: Stream[Stream[T]]) -> AsyncIterator[T]:
    async for stream in nested_st:
        async for val in stream:
            yield val


class Flatten(Stream[T]):
    def __init__(self, stream: Stream[Stream[T]]):
        self.stream = _flatten(stream)

    async def __anext__(self) -> T:
        return await anext(self.stream)


async def _flat_map(stream: Stream[T], fn: Callable[[T], Stream[U]]) -> AsyncIterator[U]:
    async for stream in stream.map(fn):
        async for val in stream:
            yield val


class FlatMap(Stream[U]):
    def __init__(self, stream: Stream[T], fn: Callable[[T], Stream[U]]):
        self.stream = _flat_map(stream, fn)

    async def __anext__(self) -> U:
        return await anext(self.stream)


class Chunks(Stream[list[T]]):
    def __init__(self, stream: Stream[T], n: int):
        self.stream = stream
        self.n = n

    async def __anext__(self) -> list[T]:
        chunk: list[T] = []
        for _ in range(self.n):
            try:
                chunk.append(await anext(self.stream))
            except StopAsyncIteration:
                break
        if not chunk:
            raise StopAsyncIteration
        return chunk


async def spawn(n):
    queue = asyncio.Queue(maxsize=n)


class ReadyChunks(Stream[list[T]]):
    def __init__(self, stream: Stream[T], n: int):
        self.n = n
        self.stream = stream
        self.pending: asyncio.Task | None = None
        self.buffer: asyncio.Queue[T] = asyncio.Queue(maxsize=n)

    async def push_anext(self) -> None:
        elem = await anext(self.stream)
        await self.buffer.put(elem)

    async def __anext__(self) -> list[T]:
        chunk: list[T] = []
        # Guarantee that we have at least one element in the buffer
        if self.pending:
            await self.pending
        else:
            await self.push_anext()

        # While we have elements in the buffer, we will return them
        for _ in range(self.n):
            try:
                chunk.append(self.buffer.get_nowait())
            except asyncio.QueueEmpty:
                return chunk

            self.pending = asyncio.create_task(self.push_anext())
            # Yield back to the event loop to allow the pending task to run
            await asyncio.sleep(0)

        return chunk


class FilterMap(Stream[U]):
    def __init__(self, stream: Stream[T], fn: Callable[[T], Optional[U]]):
        self.stream = stream
        self.fn = fn

    async def __anext__(self) -> U:
        while True:
            match self.fn(await anext(self.stream)):
                case None:
                    continue
                case result:
                    return result


async def _chain(left: Stream[T], right: Stream[T]) -> AsyncIterator[T]:
    async for val in left:
        yield val
    async for val in right:
        yield val


class Chain(Stream[T]):
    def __init__(self, left: Stream[T], right: Stream[T]):
        self.stream = _chain(left, right)

    async def __anext__(self) -> T:
        return await anext(self.stream)


class Zip(Stream[tuple[T, U]]):
    def __init__(self, left: Stream[T], right: Stream[U]):
        self.left = left
        self.right = right

    async def __anext__(self) -> tuple[T, U]:
        return (await anext(self.left), await anext(self.right))


class Enumerate(Stream[tuple[int, T]]):
    def __init__(self, stream: Stream[T]):
        self.stream = stream
        self.index = 0

    async def __anext__(self) -> tuple[int, T]:
        val = await anext(self.stream)
        idx = self.index
        self.index += 1
        return idx, val


async def _cycle(stream: Stream[T]) -> AsyncIterator[T]:
    cache: list[T] = []
    async for item in stream:
        cache.append(item)
        yield item
    if not cache:
        return
    while True:
        for item in cache:
            yield item


class Cycle(Stream[T]):
    def __init__(self, stream: Stream[T]):
        self.stream = _cycle(stream)

    async def __anext__(self) -> T:
        return await anext(self.stream)


class Scan(Stream[U]):
    def __init__(self, stream: Stream[T], acc: U, fn: Callable[[U, T], U]):
        self.stream = stream
        self.acc = acc
        self.fn = fn

    async def __anext__(self) -> U:
        val = await anext(self.stream)
        self.acc = self.fn(self.acc, val)
        return self.acc


class SkipWhile(Stream[T]):
    def __init__(self, stream: Stream[T], predicate: Callable[[T], Awaitable[bool]]):
        self.stream = stream
        self.predicate = predicate
        self.skipping = True

    async def __anext__(self) -> T:
        while True:
            val = await anext(self.stream)
            if self.skipping and await self.predicate(val):
                continue
            self.skipping = False
            return val


class TakeWhile(Stream[T]):
    def __init__(self, stream: Stream[T], predicate: Callable[[T], Awaitable[bool]]):
        self.stream = stream
        self.predicate = predicate
        self.done = False

    async def __anext__(self) -> T:
        if self.done:
            raise StopAsyncIteration
        val = await anext(self.stream)
        if await self.predicate(val):
            return val
        self.done = True
        raise StopAsyncIteration


class Take(Stream[T]):
    def __init__(self, stream: Stream[T], n: int):
        self.stream = stream
        self.remaining = n

    async def __anext__(self) -> T:
        if self.remaining <= 0:
            raise StopAsyncIteration
        self.remaining -= 1
        return await anext(self.stream)


class Skip(Stream[T]):
    def __init__(self, stream: Stream[T], n: int):
        self.stream = stream
        self.n = n
        self.skipped = False

    async def __anext__(self) -> T:
        if not self.skipped:
            for _ in range(self.n):
                await anext(self.stream)
            self.skipped = True
        return await anext(self.stream)


class TakeUntil(Stream[T], Generic[T, R]):
    def __init__(self, stream: Stream[T], fut: Awaitable[R]):
        self.stream = stream
        self._future: asyncio.Task[R] | None = asyncio.create_task(fut)
        self._result: R | None = None
        self._stopped = False

    async def __anext__(self) -> T:
        if self._stopped:
            raise StopAsyncIteration
        if self._future is None:
            return await anext(self.stream)
        next_task = asyncio.create_task(anext(self.stream))
        done, _ = await asyncio.wait(
            {next_task, self._future}, return_when=asyncio.FIRST_COMPLETED
        )
        if self._future in done:
            self._result = self._future.result()
            self._stopped = True
            next_task.cancel()
            raise StopAsyncIteration
        return next_task.result()

    def take_result(self) -> R | None:
        if not self._stopped:
            raise RuntimeError("stopping future has not resolved")
        result = self._result
        self._result = None
        return result

    def take_future(self) -> Awaitable[R] | None:
        fut = self._future
        self._future = None
        self._stopped = False
        return fut


async def _switch(st: Stream[T], coro: Callable[[T], Awaitable[U]]) -> AsyncIterator[U]:
    # Initialize a task set, with a coroutine to fetch the next item off the stream.
    tasks = task_set(anext=anext(st))

    while tasks:
        try:
            result = await select(tasks)
        except StopAsyncIteration:
            # We have exhausted the stream, but we need to wait for our coroutine
            # to yield its value downstream.
            continue

        match result:
            case ("anext", elem):
                # A new element has come available, so cancel the pending result
                # and schedule a new coroutine in its place
                tasks.cancel("result")
                tasks.update("result", coro(elem))
                tasks.update("anext", anext(st))

            case ("result", result):
                # The coroutine finished without a new item cancelling it - yield.
                yield result


class Switch(Stream[U]):
    def __init__(self, stream: Stream[T], coro: Callable[[T], Awaitable[U]]):
        self.stream = _switch(stream, coro)

    async def __anext__(self) -> U:
        return await anext(self.stream)


async def _debounce(stream: Stream[T], duration: float) -> AsyncIterator[T]:
    # Initialize a task set with tasks to get the next elem and a delay
    pending: T | None = None
    tasks = task_set(anext=anext(stream), delay=asyncio.sleep(duration))

    while tasks:
        try:
            result = await select(tasks)
        except StopAsyncIteration:
            # Stream is exhausted, but we still need to emit the pending elem once the delay elapses
            continue

        match result:
            case ("anext", elem):
                # Update the pending element with the latest item
                pending = elem

                # Push our delay further out
                tasks.cancel("delay")
                tasks.update("anext", anext(stream))
                tasks.update("delay", asyncio.sleep(duration))

            case ("delay", _):
                # Our delay has elapsed - if we have a pending result, then yield it
                if elem := pending:
                    pending = None
                    yield elem


class Debounce(Stream[T]):
    def __init__(self, stream: Stream[T], duration: float):
        self.stream = _debounce(stream, duration)

    async def __anext__(self) -> T:
        return await anext(self.stream)


async def _once(value: T) -> AsyncIterator[T]:
    yield value


class Once(Stream[T]):
    def __init__(self, value: T):
        self.stream = _once(value)

    async def __anext__(self) -> T:
        return await anext(self.stream)


class Pending(Stream[Any]):
    async def __anext__(self) -> Any:
        return await pending()


class Repeat(Stream[T]):
    def __init__(self, value: T):
        self.value = value

    async def __anext__(self) -> T:
        return self.value


class RepeatWith(Stream[T]):
    def __init__(self, fn: Callable[[], T]):
        self.fn = fn

    async def __anext__(self) -> T:
        return self.fn()


class _GenStream(Stream[T]):
    def __init__(self, gen: Iterable[T] | AsyncIterator[T]):
        if hasattr(gen, "__aiter__"):
            self.gen = gen
        else:
            self.gen = Iter(gen)

    async def __anext__(self) -> T:
        return await anext(self.gen)


class StreamSet:
    def __init__(self, streams: dict[str, Stream]):
        tasks = {}
        for name, stream in streams.items():
            tasks[name] = anext(stream)

        self._streams = streams
        self._task_set = task_set(**tasks)

    def task_set(self):
        return self._task_set

    def poll_again(self, name):
        stream = self._streams[name]
        self._task_set.update(name, anext(stream))

    def __bool__(self):
        return bool(self._task_set)
