import asyncio
import pytest

from kioto.sync import Mutex

class State:
    def __init__(self):
        self.count = 0

@pytest.mark.asyncio
async def test_mutex():
    mutex = Mutex(State)
    async with mutex.lock() as guard:
        # State can be modified via the guard
        assert guard.count == 0
        guard.count = 2
        assert guard.count == 2

    # The state is now locked and unaccessible
    with pytest.raises(ReferenceError):
        guard.count = 3

@pytest.mark.asyncio
async def test_mutex_leak():
    mutex = Mutex(State)

    async def invalid(guard):
        await asyncio.sleep(.1)
        # The guard is invalid and should raise an error
        guard.count = 2

    async with mutex.lock() as guard:
        task = asyncio.create_task(invalid(guard))

    with pytest.raises(ReferenceError):
        await task

@pytest.mark.asyncio
async def test_mutex_poison():
    mutex = Mutex(State)

    # Acquire the mutex and raise an exception
    try:
        async with mutex.lock() as guard:
            raise ValueError("Poisoning the mutex")
    except:
        pass

    # The mutex is now poisoned and should raise an error
    with pytest.raises(RuntimeError):
        async with mutex.lock() as guard:
            pass

@pytest.mark.asyncio
async def test_mutex_copy():
    mutex = Mutex(State)

    # Copying a mutex would allow for multiple threads of execution
    # to access the same state, which is not allowed
    with pytest.raises(TypeError):
        copy = mutex.__copy__()

    with pytest.raises(TypeError):
        copy = mutex.__deepcopy__({})
