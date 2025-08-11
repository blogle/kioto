from __future__ import annotations

import functools
from typing import Any, Callable, Iterable, TypeVar, AsyncIterator

from kioto import futures
from kioto.streams import impl

T = TypeVar("T")
U = TypeVar("U")


# This is the python equivalent to tokio stream::iter(iterable)
def iter(iterable: Iterable[T]) -> impl.Stream[T]:
    """
    Create a stream that yields values from the input iterable
    """
    return impl.Iter(iterable)


def once(value: T) -> impl.Stream[T]:
    """
    Create a stream that yields a single value
    """
    return impl.Once(value)


def pending() -> impl.Stream[Any]:
    """
    Create that never yields a value
    """
    return impl.Pending()


def repeat(val: T) -> impl.Stream[T]:
    """
    Create a stream which produces the same item repeatedly.
    """
    return impl.Repeat(val)


def repeat_with(fn: Callable[[], T]) -> impl.Stream[T]:
    """
    Create a stream with produces values by repeatedly calling the input fn
    """
    return impl.RepeatWith(fn)


def async_stream(f: Callable[..., AsyncIterator[T]]):
    """
    Decorator that converts an async generator function into a Stream object
    """

    @functools.wraps(f)
    def stream(*args, **kwargs) -> impl.Stream[T]:
        # Take an async generator function and return a Stream object
        # that inherits all of the stream methods
        return impl.Stream.from_generator(f(*args, **kwargs))

    return stream


# def stream_set(**streams):
#    return impl.StreamSet(streams)


@async_stream
async def select(**streams: impl.Stream[Any]) -> AsyncIterator[tuple[str, Any]]:
    group = impl.StreamSet(streams)
    while group.task_set():
        try:
            name, result = await futures.select(group.task_set())
        except StopAsyncIteration:
            continue
        else:
            group.poll_again(name)

        yield name, result
