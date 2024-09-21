import aiofiles

from typing import Any, Callable, Iterable
from kioto.sink import impl

class FileSink(impl.Sink):
    """
    Sink implementation that writes items to a file asynchronously.
    """

    def __init__(self, file_path: str):
        self._file_path = file_path
        self._file = None
        self._closed = False

    async def _ensure_file_open(self):
        if self._file is None:
            self._file = await aiofiles.open(self._file_path, mode='a')

    async def feed(self, item: Any):
        if self._closed:
            raise RuntimeError("Cannot feed to a closed Sink.")
        await self._ensure_file_open()
        await self._file.write(f"{item}\n")

    async def send(self, item: Any):
        if self._closed:
            raise RuntimeError("Cannot send to a closed Sink.")
        await self.feed(item)
        await self.flush()

    async def flush(self):
        if self._file:
            await self._file.flush()

    async def close(self):
        if not self._closed:
            self._closed = True
            if self._file:
                await self._file.flush()
                await self._file.close()
                self._file = None

def drain():
    return impl.Drain()