import asyncio
from typing import Dict, Any, Tuple, Optional

class TaskSet:
    def __init__(self, tasks: Dict[str, Any]):
        """
        Initialize the TaskGroup with a dictionary of named coroutines.

        :param tasks: A dictionary mapping task names to coroutine objects.
        """
        self._tasks = {
            name: asyncio.create_task(coro, name=name) for name, coro in tasks.items()
        }

    def __bool__(self) -> bool:
        """
        Return True if there are any tasks in the group, False otherwise.
        """
        return bool(self._tasks)

    def get_tasks(self):
        """
        Retrieve all active asyncio.Task instances in the group.
        """
        return self._tasks.values()

    def pop_task(self, task: asyncio.Task) -> Optional[str]:
        """
        Remove a completed task from the group and return its name.

        :param task: The asyncio.Task instance to remove.
        :return: The name of the removed task, or None if not found.
        """
        name = task.get_name()
        self._tasks.pop(name, None)
        return name

    def update(self, name: str, coro: Any):
        """
        Add a new coroutine to the group with the specified name.

        :param name: The unique name for the new task.
        :param coro: The coroutine object to be added as a task.
        :raises ValueError: If a task with the given name already exists.
        """
        if name in self._tasks:
            raise ValueError(
                f"Task with name '{name}' already exists. Must be cancelled before update."
            )
        self._tasks[name] = asyncio.create_task(coro, name=name)

    def cancel(self, name: str):
        """
        Cancel a task by its name.

        :param name: The name of the task to cancel.
        """
        task = self._tasks.pop(name, None)
        if task and not task.done():
            task.cancel()

    def cancel_all(self):
        """
        Cancel all active tasks in the group.
        """
        for task in self._tasks.values():
            if task and not task.done():
                task.cancel()
        self._tasks.clear()

class Shared:

    def __init__(self, coro):
        self._coro = coro
        self._result = None
        self._lock = asyncio.Lock()

    def __await__(self):
        async def shared_coro():
            # If the result has already been resolved return it
            if self._result is not None:
                if isinstance(self._result, Exception):
                    raise self._result
                return self._result

            async with self._lock:
                # Its possible that the result was resolved while
                # we waited to acquire the lock - if so return it.
                if self._result is not None:
                    if isinstance(self._result, Exception):
                        raise self._result
                    return self._result

                # Otherwise we need to evaluate the coroutine and cache the result
                try:
                    self._result = await self._coro
                except Exception as e:
                    self._result = e
                    raise self._result

                return self._result

        return shared_coro().__await__()
