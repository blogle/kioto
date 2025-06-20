import asyncio
from typing import Tuple, Any

from kioto.futures import impl


async def ready(result: Any) -> Any:
    """
    A simple coroutine that immediately returns the given result.
    """
    return result


def task_set(**tasks) -> impl.TaskSet:
    """
    Convenience function to create a TaskSet using keyword arguments.

    Args:
        **tasks: Arbitrary keyword arguments where each key is the task name and each value is a coroutine.

    Returns:
        TaskSet: An instance of TaskSet containing the provided tasks.
    """
    if not all(asyncio.iscoroutine(task) for task in tasks.values()):
        raise ValueError("All arguments to task_set must be coroutine objects.")
    return impl.TaskSet(tasks)


async def select(task_set: impl.TaskSet) -> Tuple[str, Any]:
    """
    Await the first task in ``task_set`` to complete.

    Returns a tuple of the task name and the result of that task. ``result`` can
    be a normal value, an exception instance, or an ``asyncio.CancelledError`` if
    the task was cancelled. Callers are responsible for handling the returned
    exceptions.

    :param task_set: The ``TaskSet`` containing tasks to monitor.
    :return: ``Tuple[str, Any]`` representing the completed task and its result.
    :raises ValueError: If ``task_set`` is empty.
    """
    if not task_set:
        raise ValueError("select called on an empty TaskSet! - nothing to poll")

    done, _ = await asyncio.wait(
        task_set.get_tasks(), return_when=asyncio.FIRST_COMPLETED
    )

    result = None
    for task in done:
        cancelled = False
        if task in task_set._cancelled_tasks:
            name = task_set._cancelled_tasks.pop(task)
            cancelled = True
        else:
            name = task_set.pop_task(task)

        try:
            task_result = task.result()
        except Exception as e:
            task_result = e

        if result is None:
            result = (name, task_result)
        else:
            if not cancelled:
                task_set.update(name, ready(task_result))

    name, value = result
    return name, value


async def pending():
    """Returns a coroutine that never completes."""
    # The futures result is never set - so it will never complete
    return await asyncio.Future()


def shared(coro):
    """Returns a handle to a future allowing multiple tasks to await it."""
    return impl.Shared(coro)


async def lazy(fn):
    """Wraps a callable into a coroutine that evaluates the function when awaited."""
    return fn()


async def try_join(*coros):
    """
    Awaits all coroutines in the provided list and returns a list of their results.

    If an exception is encountered, its raised and the remaining tasks are cancelled.
    """
    try:
        pending = []
        async with asyncio.TaskGroup() as group:
            for coro in coros:
                pending.append(group.create_task(coro))
        return [task.result() for task in pending]
    except Exception as e:
        raise e.exceptions[0]
