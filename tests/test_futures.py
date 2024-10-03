import asyncio
import pytest

from kioto.futures.impl import TaskSet
from kioto.futures.api import ready, select, task_set, pending, shared, lazy, try_join


@pytest.mark.asyncio
async def test_select_returns_first_completed():
    async def coro1():
        await asyncio.sleep(0.2)
        return "result1"

    async def coro2():
        await asyncio.sleep(0.1)
        return "result2"

    ts = task_set(task1=coro1(), task2=coro2())

    name, result = await select(ts)
    assert name == "task2"
    assert result == "result2"

    # Now, the remaining task should still be in the TaskSet
    assert "task1" in ts._tasks
    assert "task2" not in ts._tasks

    # Select the next task
    name, result = await select(ts)
    assert name == "task1"
    assert result == "result1"

    # TaskSet should now be empty
    assert not ts


@pytest.mark.asyncio
async def test_select_multiple_completions():
    async def coro1():
        await asyncio.sleep(0.1)
        return "result1"

    async def coro2():
        await asyncio.sleep(0.1)
        return "result2"

    async def coro3():
        await asyncio.sleep(0.2)
        return "result3"

    ts = task_set(task1=coro1(), task2=coro2(), task3=coro3())

    # Select first completed task (either task1 or task2)
    name1, result1 = await select(ts)
    assert name1 in ["task1", "task2"]
    assert result1 in ["result1", "result2"]

    # Select next completed task
    name2, result2 = await select(ts)
    assert name2 in ["task1", "task2"]
    assert result2 in ["result1", "result2"]
    assert name2 != name1

    # Select the last task
    name3, result3 = await select(ts)
    assert name3 == "task3"
    assert result3 == "result3"

    assert not ts


@pytest.mark.asyncio
async def test_select_propagates_exceptions():
    async def coro1():
        await asyncio.sleep(0.1)
        raise ValueError("An error occurred in coro1")

    async def coro2():
        await asyncio.sleep(0.2)
        return "result2"

    ts = task_set(task1=coro1(), task2=coro2())

    with pytest.raises(ValueError, match="An error occurred in coro1"):
        await select(ts)

    # After exception, coro2 should still be in the TaskSet
    assert "task2" in ts._tasks
    assert "task1" not in ts._tasks

    # Now, select the remaining task
    name, result = await select(ts)
    assert name == "task2"
    assert result == "result2"

    assert not ts


@pytest.mark.asyncio
async def test_task_set_empty():
    ts = TaskSet({})

    # Calling select on an empty task set should raise an exception
    with pytest.raises(ValueError):
        await select(ts)


@pytest.mark.asyncio
async def test_task_set_cancellation():
    async def coro1():
        await asyncio.sleep(1)
        return "result1"

    async def coro2():
        await asyncio.sleep(2)
        return "result2"

    ts = task_set(task1=coro1(), task2=coro2())

    # Cancel task1
    ts.cancel("task1")
    assert "task1" not in ts._tasks

    # Select should return task2 after 2 seconds
    name, result = await select(ts)
    assert name == "task2"
    assert result == "result2"

    assert not ts


@pytest.mark.asyncio
async def test_task_set_dynamic_addition():
    async def coro1():
        await asyncio.sleep(0.1)
        return "result1"

    async def coro2():
        await asyncio.sleep(0.2)
        return "result2"

    async def coro3():
        await asyncio.sleep(0.0)
        return "result3"

    ts = task_set(task1=coro1(), task2=coro2())

    name, result = await select(ts)
    assert name == "task1"
    assert result == "result1"

    # Dynamically add coro3
    ts.update("task3", coro3())

    name, result = await select(ts)
    assert name == "task3"
    assert result == "result3"

    name, result = await select(ts)
    assert name == "task2"
    assert result == "result2"

    assert not ts


@pytest.mark.asyncio
async def test_task_set_duplicate_task_names():
    async def coro1():
        await asyncio.sleep(0.1)
        return "result1"

    ts = task_set(task1=coro1())

    new_task = coro1()
    with pytest.raises(ValueError, match="Task with name 'task1' already exists"):
        ts.update("task1", new_task)

    # Await the task to silence warnings
    await new_task


@pytest.mark.asyncio
async def test_task_set_cancel_all():
    async def coro1():
        await asyncio.sleep(1)
        return "result1"

    async def coro2():
        await asyncio.sleep(1)
        return "result2"

    ts = task_set(task1=coro1(), task2=coro2())
    tasks = ts.get_tasks()

    ts.cancel_all()
    assert not ts._tasks

    # Ensure tasks are cancelled
    for task in tasks:
        assert task.done()


@pytest.mark.asyncio
async def test_ready_coroutine():
    result = "test_result"
    res = await ready(result)
    assert res == result


@pytest.mark.asyncio
async def test_task_set_non_coroutine_input():
    with pytest.raises(
        ValueError, match="All arguments to task_set must be coroutine objects."
    ):
        ts = task_set(task1="not a coroutine")


@pytest.mark.asyncio
async def test_task_set_partial_completion():
    async def coro1():
        await asyncio.sleep(0.1)
        return "result1"

    async def coro2():
        await asyncio.sleep(0.2)
        return "result2"

    ts = task_set(task1=coro1(), task2=coro2())

    # Select first task
    name1, result1 = await select(ts)
    assert name1 == "task1"
    assert result1 == "result1"

    # Only task2 remains
    assert "task2" in ts._tasks
    assert "task1" not in ts._tasks

    # Now cancel task2
    ts.cancel("task2")
    assert not ts

    # Attempting to select should raise ValueError
    with pytest.raises(ValueError):
        await select(ts)

@pytest.mark.asyncio
async def test_pending():
    """
    Test that `pending` coroutine never completes.
    """
    # Create a task for pending
    task = asyncio.create_task(pending())

    # Wait for a short duration to ensure it's still pending
    await asyncio.sleep(0.1)
    assert not task.done()

    # Optionally, cancel the task to clean up
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

@pytest.mark.asyncio
async def test_shared_multiple_awaiters():
    """
    Test that multiple awaiters receive the same result from a Shared coroutine.
    """
    execution_count = 0

    async def coro():
        nonlocal execution_count
        execution_count += 1
        await asyncio.sleep(0.1)
        return "shared_result"

    shared_handle = shared(coro())

    # Unlike create_task, shared does nothing until awaited
    assert execution_count == 0

    # Create multiple awaiters
    result1 = await shared_handle
    result2 = await shared_handle
    result3 = await shared_handle

    assert result1 == "shared_result"
    assert result2 == "shared_result"
    assert result3 == "shared_result"
    assert execution_count == 1  # Ensure the coroutine was executed only once

@pytest.mark.asyncio
async def test_shared_exception_propagation():
    """
    Test that exceptions raised by the Shared coroutine are propagated to all awaiters.
    """
    async def coro():
        await asyncio.sleep(0.1)
        raise ValueError("Test exception in Shared coroutine")

    shared_handle = shared(coro())

    # Define a helper to await and capture exceptions
    async def await_shared():
        try:
            await shared_handle
        except Exception as e:
            return e

    # Create multiple awaiters
    exception1 = await await_shared()
    exception2 = await await_shared()
    exception3 = await await_shared()

    assert isinstance(exception1, ValueError)
    assert str(exception1) == "Test exception in Shared coroutine"
    assert isinstance(exception2, ValueError)
    assert str(exception2) == "Test exception in Shared coroutine"
    assert isinstance(exception3, ValueError)
    assert str(exception3) == "Test exception in Shared coroutine"

@pytest.mark.asyncio
async def test_shared_exception_execution_count():
    """
    Test that the Shared coroutine is executed only once even if it raises an exception.
    """
    execution_count = 0

    async def coro():
        nonlocal execution_count
        execution_count += 1
        await asyncio.sleep(0.1)
        raise RuntimeError("Shared coroutine error")

    shared_handle = shared(coro())

    # Define a helper to await and capture exceptions
    async def await_shared():
        try:
            await shared_handle
        except Exception as e:
            return e

    # Create multiple awaiters
    exception1 = await await_shared()
    exception2 = await await_shared()

    assert isinstance(exception1, RuntimeError)
    assert str(exception1) == "Shared coroutine error"
    assert isinstance(exception2, RuntimeError)
    assert str(exception2) == "Shared coroutine error"
    assert execution_count == 1  # Ensure the coroutine was executed only once

@pytest.mark.asyncio
async def test_lazy():
    """
    Test that `lazy` evaluates the function when awaited.
    """
    def add(a, b):
        return a + b

    lazy_coro = lazy(lambda: add(2, 3))
    result = await lazy_coro
    assert result == 5

    # Test with a function that raises an exception
    def raise_error():
        raise ValueError("Test error")

    lazy_error = lazy(raise_error)
    with pytest.raises(ValueError, match="Test error"):
        await lazy_error

@pytest.mark.asyncio
async def test_try_join():

    async def a():
        return "a"

    async def b():
        return "b"

    results = await try_join(a(), b())
    assert results == ["a", "b"]

@pytest.mark.asyncio
async def test_try_join_exception():
    async def a():
        raise ValueError("fail")

    async def b():
        return "b"

    with pytest.raises(ValueError):
        await try_join(a(), b())