import asyncio
from typing import Any, Callable

class Sink:

    async def feed(self, item: Any):
        raise NotImplementedError

    async def send(self, item: Any):
        raise NotImplementedError

    async def flush(self):
        raise NotImplementedError

    async def close(self):
        raise NotImplementedError

    async def send_all(self, stream: "Stream"):
        async for item in stream:
            await self.feed(item)
        await self.flush()

    def with_map(self, fn: Callable[[Any], Any]) -> "With":
        return With(self, fn)

    def buffer(self, capacity: int) -> "Buffer":
        return Buffer(self, capacity)

    def fanout(self, other: "Sink") -> "Fanout":
        return Fanout(self, other)

class Drain(Sink):

    async def feed(self, item: Any):
        pass

    async def send(self, item: Any):
        pass

    async def flush(self):
        pass

    async def close(self):
        pass

class With(Sink):

    def __init__(self, sink: Sink, fn: Callable[[Any], Any]):
        self._fn = fn
        self._sink = sink
        self._closed = False

    async def feed(self, item: Any):
        if self._closed:
            raise RuntimeError("Cannot feed to a closed Sink.")
        transformed = self._fn(item)
        await self._sink.feed(transformed)

    async def send(self, item: Any):
        if self._closed:
            raise RuntimeError("Cannot send to a closed Sink.")
        transformed = self._fn(item)
        await self._sink.send(transformed)

    async def flush(self):
        if self._closed:
            raise RuntimeError("Cannot flush a closed Sink.")
        await self._sink.flush()

    async def close(self):
        if not self._closed:
            self._closed = True
            await self._sink.close()


class Buffer(Sink):

    def __init__(self, sink: Sink, capacity: int):
        self._sink = sink
        self._queue = asyncio.Queue(maxsize=capacity)
        self._worker = asyncio.create_task(self._poll_queue())
        self._closed = False

    async def _poll_queue(self):
        try:
            while True:
                item = await self._queue.get()
                await self._sink.feed(item)
                self._queue.task_done()
        except asyncio.CancelledError:
            # Optionally, handle remaining items or cleanup
            while not self._queue.empty():
                try:
                    item = self._queue.get_nowait()
                    await self._sink.feed(item)
                    self._queue.task_done()
                except asyncio.QueueEmpty:
                    break
            pass
        except Exception as e:
            print(f"Error in Buffer._poll_queue: {e}")
            raise

    async def feed(self, item: Any):
        if self._closed:
            raise RuntimeError("Cannot feed to a closed Sink.")
        await self._queue.put(item)

    async def send(self, item: Any):
        if self._closed:
            raise RuntimeError("Cannot send to a closed Sink.")
        await self._queue.put(item)
        await self.flush()

    async def flush(self):
        if self._closed:
            raise RuntimeError("Cannot flush a closed Sink.")
        await self._queue.join()
        await self._sink.flush()

    async def close(self):
        if not self._closed:
            self._worker.cancel()

            try:
                await self._worker
            except asyncio.CancelledError:
                pass

            await self.flush()
            await self._sink.close()
            self._closed = True


class Fanout(Sink):

    def __init__(self, *sinks: Sink):
        self._sinks = sinks
        self._closed = False

    async def feed(self, item: Any):
        if self._closed:
            raise RuntimeError("Cannot feed to a closed Sink.")
        async with asyncio.TaskGroup() as feed_group:
            for sink in self._sinks:
                feed_group.create_task(
                    sink.feed(item)
                )

    async def send(self, item: Any):
        if self._closed:
            raise RuntimeError("Cannot send to a closed Sink.")
        async with asyncio.TaskGroup() as send_group:
            for sink in self._sinks:
                send_group.create_task(
                    sink.send(item)
                )

    async def flush(self):
        if self._closed:
            raise RuntimeError("Cannot flush a closed Sink.")
        async with asyncio.TaskGroup() as flush_group:
            for sink in self._sinks:
                flush_group.create_task(
                    sink.flush()
                )

    async def close(self):
        if not self._closed:
            self._closed = True
            async with asyncio.TaskGroup() as close_group:
                for sink in self._sinks:
                    close_group.create_task(
                        sink.close()
                    )
