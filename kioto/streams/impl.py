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
    ...


async def _queue_stream(queue):
    while True:
        task = await queue.get()
        if isinstance(task, _Sentinel):
            return
        yield await task

async def _buffered(stream, n):
    task_queue = asyncio.Queue(n)

    # Here we create two tasks that we race against one another.
    # The first tries to queue as many tasks as it can while the
    # other runs the next task. 
    push_work = (
        stream
            .map(asyncio.create_task)
            .then(task_queue.put)
    )
    
    it = _queue_stream(task_queue)

    tasks = task_set(
       spawn=anext(push_work),
       results=anext(it)
    )

    while tasks:
        # Eventually we will exhaust the underlying stream. At this point
        # we need to signal the task queue iterator that we have reached the
        # end. The buffered stream is exhausted once both tasks exit.
        try:
            completion = await select(tasks)
        except StopAsyncIteration:
            await task_queue.put(_Sentinel())
            continue

        match completion:
            case ("spawn", coro):
                tasks.update("spawn", anext(push_work))
            
            case ("results", result):
                tasks.update("results", anext(it))
                yield result


class Buffered(Stream):
    def __init__(self, stream, n):
        self.stream = _buffered(stream, n)

    async def __anext__(self):
        return await anext(self.stream)


async def _buffered_unordered(stream, n):
    tasks = task_set(spawn=anext(stream))
    notification = asyncio.Event()
    slots = set(range(n))

    async def spawn_later(coro):
        await notification.wait()
        return coro

    while tasks:
        try:
            completion = await select(tasks)
        except StopAsyncIteration:
            continue

        match completion:

            case ("spawn", coro):
                try:
                    task_id = slots.pop()
                except KeyError:
                    notification.clear()
                    tasks.update("spawn", spawn_later(coro))
                else:
                    tasks.update(str(task_id), coro)
                    tasks.update("spawn", anext(stream))


            case (task_id, result):
                # Make this worker available for more work
                slots.add(int(task_id))
                notification.set()
                yield result
        

class BufferedUnordered(Stream):
    def __init__(self, stream, n):
        self.stream = _buffered_unordered(stream, n)

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
