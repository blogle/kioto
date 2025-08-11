import builtins


class _AsyncFile:
    def __init__(self, fp):
        self._fp = fp

    async def write(self, data):
        self._fp.write(data)

    async def flush(self):
        self._fp.flush()

    async def close(self):
        self._fp.close()


async def open(file, mode="r"):
    return _AsyncFile(builtins.open(file, mode))

