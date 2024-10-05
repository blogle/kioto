import asyncio
import contextlib
import weakref

class Guard:
    def __init__(self, state):
        object.__setattr__(self, "_Guard__state", state)

    def __getattr__(self, name):
        return getattr(self.__state, name)

    def __setattr__(self, name, value):
        return setattr(self.__state, name, value)

    def __delattr__(self, name):
        return delattr(self.__state, name)


@contextlib.asynccontextmanager
async def mutex_state_manager(mutex):
    state = mutex._state
    lock = mutex._lock
    await lock.acquire()
    guard = Guard(state)
    try:
        yield weakref.proxy(guard)
    except Exception as e:
        mutex._poisoned = True
    finally:
        del guard
        lock.release()


class Mutex:
    def __init__(self, init_fn):
        self._state = init_fn()
        self._lock = asyncio.Lock()
        self._poisoned = False

    def lock(self):
        if self._poisoned:
            raise RuntimeError("Mutex is poisoned")
        return mutex_state_manager(self)

    def __copy__(self):
        raise TypeError("Mutex instances cannot be copied")

    def __deepcopy__(self, memo):
        raise TypeError("Mutex instances cannot be deep copied.")

