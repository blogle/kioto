import asyncio
import builtins
import collections

from typing import Dict

from kioto.futures import pending, select, task_set


class Stream:
    def __aiter__(self):
        return self

    @staticmethod
    def from_generator(gen):
        return _GenStream(gen)

    def map(self, fn):
        return Map(self, fn)

    def then(self, fn):
        return Then(self, fn)

    def filter(self, predicate):
        return Filter(self, predicate)

    def buffered(self, n):
        return Buffered(self, n)

    def buffered_unordered(self, n):
        return BufferedUnordered(self, n)

    def flatten(self):
        return Flatten(self)

    def flat_map(self, fn):
        return FlatMap(self, fn)

    def chunks(self, n):
        return Chunks(self, n)

    def ready_chunks(self, n):
        return ReadyChunks(self, n)

    def filter_map(self, fn):
        return FilterMap(self, fn)

    def chain(self, stream):
        return Chain(self, stream)

    def zip(self, stream):
        return Zip(self, stream)

    async def fold(self, fn, acc):
        async for val in self:
            acc = fn(acc, val)
        return acc

    async def collect(self):
        return [i async for i in aiter(self)]


class Iter(Stream):
    def __init__(self, iterable):
        self.iterable = builtins.iter(iterable)

    async def __anext__(self):
        try:
            return next(self.iterable)
        except StopIteration:
            raise StopAsyncIteration


class Map(Stream):
    def __init__(self, stream, fn):
        self.fn = fn
        self.stream = stream

    async def __anext__(self):
        return self.fn(await anext(self.stream))


class Then(Stream):
    def __init__(self, stream, fn):
        self.fn = fn
        self.stream = stream

    async def __anext__(self):
        arg = await anext(self.stream)
        return await self.fn(arg)


class Filter(Stream):
    def __init__(self, stream, predicate):
        self.predicate = predicate
        self.stream = stream

    async def __anext__(self):
        while True:
            val = await anext(self.stream)
            if self.predicate(val):
                return val

class _Sentinel:
    """Special marker object used to signal the end of the stream."""
    def __repr__(self):
        return "<_Sentinel>"

async def _queue_stream(result_queue: asyncio.Queue):
    """
    Asynchronously yield results from tasks as they complete.
    
    This function waits for tasks to be put into the result_queue.
    When it retrieves the sentinel value, it stops iteration.
    """
    while True:
        task_result = await result_queue.get()
        if isinstance(task_result, _Sentinel):
            return
        # Propagate the task result (or exception if the task failed)
        yield task_result

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
    result_queue = asyncio.Queue(buffer_size)

    # Convert each element from the stream into a task and push into the result_queue.
    # When the stream is exhausted, enqueue a sentinel value.
    push_tasks = (
        stream.map(asyncio.create_task)
        .chain(Once(_Sentinel()))
        .then(result_queue.put)
        .collect()
    )

    # Start a task that spawns tasks from the stream.
    spawner_task = asyncio.create_task(push_tasks)

    # Create a generator that yields results from the result_queue.
    async for result in _queue_stream(result_queue):
        yield await result

    # Ensure the spawner task is awaited in case it is still running.
    await spawner_task

class Buffered(Stream):
    """
    Buffered stream that spawns tasks from an underlying stream with a specified buffer size.
    
    Results are yielded as soon as individual tasks complete.
    """
    def __init__(self, stream, buffer_size: int):
        self.stream = _buffered(stream, buffer_size)

    async def __anext__(self):
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


class BufferedUnordered(Stream):
    """
    Stream implementation that yields results from tasks in an unordered fashion.
    
    It buffers tasks from the underlying stream up to 'buffer_size' concurrently.
    As soon as any task completes, its result is yielded and its slot is freed for reuse.
    """
    def __init__(self, stream, buffer_size: int):
        self.stream = _buffered_unordered(stream, buffer_size)

    async def __anext__(self):
        return await anext(self.stream)


async def _flatten(nested_st):
    async for stream in nested_st:
        async for val in stream:
            yield val


class Flatten(Stream):
    def __init__(self, stream):
        self.stream = _flatten(stream)

    async def __anext__(self):
        return await anext(self.stream)


async def _flat_map(stream, fn):
    async for stream in stream.map(fn):
        async for val in stream:
            yield val


class FlatMap(Stream):
    def __init__(self, stream, fn):
        self.stream = _flat_map(stream, fn)

    async def __anext__(self):
        return await anext(self.stream)


class Chunks(Stream):
    def __init__(self, stream, n):
        self.stream = stream
        self.n = n

    async def __anext__(self):
        chunk = []
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


class ReadyChunks(Stream):
    def __init__(self, stream, n):
        self.n = n
        self.stream = stream
        self.pending = None
        self.buffer = asyncio.Queue(maxsize=n)

    async def push_anext(self):
        elem = await anext(self.stream)
        await self.buffer.put(elem)

    async def __anext__(self):
        chunk = []
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


class FilterMap(Stream):
    def __init__(self, stream, fn):
        self.stream = stream
        self.fn = fn

    async def __anext__(self):
        while True:
            match self.fn(await anext(self.stream)):
                case None:
                    continue
                case result:
                    return result


async def _chain(left, right):
    async for val in left:
        yield val
    async for val in right:
        yield val


class Chain(Stream):
    def __init__(self, left, right):
        self.stream = _chain(left, right)

    async def __anext__(self):
        return await anext(self.stream)


class Zip(Stream):
    def __init__(self, left, right):
        self.left = left
        self.right = right

    async def __anext__(self):
        return (await anext(self.left), await anext(self.right))


async def _once(value):
    yield value


class Once(Stream):
    def __init__(self, value):
        self.stream = _once(value)

    async def __anext__(self):
        return await anext(self.stream)


class Pending(Stream):
    async def __anext__(self):
        return await pending()


class Repeat(Stream):
    def __init__(self, value):
        self.value = value

    async def __anext__(self):
        return self.value


class RepeatWith(Stream):
    def __init__(self, fn):
        self.fn = fn

    async def __anext__(self):
        return self.fn()


class _GenStream(Stream):
    def __init__(self, gen):
        if hasattr(gen, "__aiter__"):
            self.gen = gen
        else:
            self.gen = Iter(gen)

    async def __anext__(self):
        return await anext(self.gen)


class StreamSet:
    def __init__(self, streams: Dict[str, Stream]):
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
