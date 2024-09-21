import functools
from kioto.streams import impl


# This is the python equivalent to tokio stream::iter(iterable)
def iter(iterable) -> impl.Stream:
    """
    Create a stream that yields values from the input iterable
    """
    return impl.Iter(iterable)


def once(value) -> impl.Stream:
    """
    Create a stream that yields a single value
    """
    return impl.Once(value)


def pending() -> impl.Stream:
    """
    Create that never yields a value
    """
    return impl.Pending()


def repeat(val: any) -> impl.Stream:
    """
    Create a stream which produces the same item repeatedly.
    """
    return impl.Repeat(val)


def repeat_with(fn: callable) -> impl.Stream:
    """
    Create a stream with produces values by repeatedly calling the input fn
    """
    return impl.RepeatWith(fn)


def async_stream(f):
    """
    Decorator that converts an async generator function into a Stream object
    """

    @functools.wraps(f)
    def stream(*args, **kwargs) -> impl.Stream:
        # Take an async generator function and return a Stream object
        # that inherits all of the stream methods
        return impl.Stream.from_generator(f(*args, **kwargs))

    return stream
