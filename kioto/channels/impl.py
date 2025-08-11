from __future__ import annotations

import asyncio
import threading
import weakref

from collections import deque
from typing import Callable, Deque, Generic, TypeVar, AsyncIterator, Awaitable

from kioto.streams import Stream
from kioto.sink import Sink
from kioto.internal.buffer import BufferPool

from . import error


T = TypeVar("T")


def notify_one(waiters):
    if waiters:
        tx = waiters.pop()
        tx.send(())


def notify_all(waiters):
    while waiters:
        tx = waiters.pop()
        if not tx._channel.done():
            tx.send(())


def wait_for_notice(waiters: Deque[OneShotSender[None]]) -> Awaitable[None]:
    # Create a oneshot channel
    channel: OneShotChannel[None] = OneShotChannel()
    sender: OneShotSender[None] = OneShotSender(channel)
    receiver: OneShotReceiver[None] = OneShotReceiver(channel)

    # register the tx side for notification
    waiters.append(sender)

    return receiver()


def round_to_power_of_2(n: int) -> int:
    """Round up to the nearest power of 2."""
    if n <= 0:
        return 1
    if n & (n - 1) == 0:  # already power of 2
        return n

    # Find the next power of 2
    power = 1
    while power < n:
        power <<= 1
    return power


class SPSCBuffer:
    """
    Single Producer Single Consumer lock-free buffer for bytes.
    """

    def __init__(self, capacity: int):
        # Round capacity to power of 2 for optimization
        self._capacity = round_to_power_of_2(capacity)
        self._mask = self._capacity - 1  # For fast modulo with bitmask

        # Circular buffer
        self._buffer = bytearray(self._capacity)

        # Head and tail pointers (atomically updated)
        self._head = 0  # Reader position
        self._tail = 0  # Writer position

        # Tracking sender/receiver instances (weak references only)
        self._sender_ref = None
        self._receiver_ref = None

        # Async notification - single waiter per side for SPSC
        self._reader_waiter = None
        self._writer_waiter = None

        self._lock = threading.Lock()

    def _available_space(self) -> int:
        """Get available space for writing."""
        return self._capacity - self._size()

    def _size(self) -> int:
        """Get current number of bytes in buffer."""
        return (self._tail - self._head) & ((2 * self._capacity) - 1)

    def _register_sender(self, sender: "SPSCSender"):
        """Register a sender, ensuring only one exists."""
        if self._sender_ref is not None:
            raise RuntimeError("Only one sender allowed in SPSC buffer")

        self._sender_ref = weakref.ref(sender, self._sender_dropped)

    def _register_receiver(self, receiver: "SPSCReceiver"):
        """Register a receiver, ensuring only one exists."""
        if self._receiver_ref is not None:
            raise RuntimeError("Only one receiver allowed in SPSC buffer")

        self._receiver_ref = weakref.ref(receiver, self._receiver_dropped)

    def _sender_dropped(self, ref):
        """Called when sender is garbage collected."""
        self._sender_ref = None
        if self._reader_waiter is not None:
            self._reader_waiter.send(())
            self._reader_waiter = None

    def _receiver_dropped(self, ref):
        """Called when receiver is garbage collected."""
        self._receiver_ref = None
        if self._writer_waiter is not None:
            self._writer_waiter.send(())
            self._writer_waiter = None

    def _has_sender(self) -> bool:
        return self._sender_ref is not None

    def _has_receiver(self) -> bool:
        return self._receiver_ref is not None

    def _push_bytes(self, data: bytes) -> int:
        """
        Push bytes to buffer, returns number of bytes successfully written.
        This is lock-free for the sender.
        """
        if not self._has_receiver():
            raise error.ReceiversDisconnected

        available = self._available_space()
        to_write = min(len(data), available)

        if to_write == 0:
            return 0

        # Write data to circular buffer
        tail_pos = self._tail & self._mask

        if tail_pos + to_write <= self._capacity:
            # No wrap around
            self._buffer[tail_pos : tail_pos + to_write] = data[:to_write]
        else:
            # Handle wrap around
            first_part = self._capacity - tail_pos
            self._buffer[tail_pos:] = data[:first_part]
            self._buffer[: to_write - first_part] = data[first_part:to_write]

        # Update tail atomically
        self._tail = (self._tail + to_write) & ((2 * self._capacity) - 1)

        return to_write

    def _pop_bytes(self, size: int) -> bytearray:
        """
        Pop bytes from buffer, returns up to size bytes as mutable buffer.
        This is lock-free for the receiver.
        """
        if not self._has_sender() and self._size() == 0:
            raise error.SendersDisconnected

        available = self._size()
        to_read = min(size, available)

        if to_read == 0:
            return bytearray()

        # Read data from circular buffer
        head_pos = self._head & self._mask

        if head_pos + to_read <= self._capacity:
            # No wrap around
            result = bytearray(self._buffer[head_pos : head_pos + to_read])
        else:
            # Handle wrap around
            first_part = self._capacity - head_pos
            result = bytearray(
                self._buffer[head_pos:] + self._buffer[: to_read - first_part]
            )

        # Update head atomically
        self._head = (self._head + to_read) & ((2 * self._capacity) - 1)

        return result

    def _pop_into(self, out: bytearray) -> int:
        """
        Pop bytes into existing buffer, returns number of bytes copied.
        This is lock-free for the receiver.
        """
        if not self._has_sender() and self._size() == 0:
            raise error.SendersDisconnected

        available = self._size()
        to_read = min(len(out), available)

        if to_read == 0:
            return 0

        # Read data from circular buffer into output buffer
        head_pos = self._head & self._mask

        if head_pos + to_read <= self._capacity:
            # No wrap around
            out[:to_read] = self._buffer[head_pos : head_pos + to_read]
        else:
            # Handle wrap around
            first_part = self._capacity - head_pos
            out[:first_part] = self._buffer[head_pos:]
            out[first_part:to_read] = self._buffer[: to_read - first_part]

        # Update head atomically
        self._head = (self._head + to_read) & ((2 * self._capacity) - 1)

        return to_read

    def _notify_reader(self):
        """Notify waiting reader that data is available."""
        if self._reader_waiter is not None:
            self._reader_waiter.send(())
            self._reader_waiter = None

    def _notify_writer(self):
        """Notify waiting writer that space is available."""
        if self._writer_waiter is not None:
            self._writer_waiter.send(())
            self._writer_waiter = None

    async def _wait_for_reader(self):
        """Wait for reader to consume data."""
        if self._writer_waiter is not None:
            raise RuntimeError("Only one writer can wait at a time in SPSC buffer")

        # Create a oneshot channel
        channel = OneShotChannel()
        sender = OneShotSender(channel)
        receiver = OneShotReceiver(channel)

        # Store the sender as our waiter
        self._writer_waiter = sender

        try:
            await receiver()
        finally:
            # Clean up waiter if it's still ours
            if self._writer_waiter is sender:
                self._writer_waiter = None

    async def _wait_for_writer(self):
        """Wait for writer to produce data."""
        if self._reader_waiter is not None:
            raise RuntimeError("Only one reader can wait at a time in SPSC buffer")

        # Create a oneshot channel
        channel = OneShotChannel()
        sender = OneShotSender(channel)
        receiver = OneShotReceiver(channel)

        # Store the sender as our waiter
        self._reader_waiter = sender

        try:
            await receiver()
        finally:
            # Clean up waiter if it's still ours
            if self._reader_waiter is sender:
                self._reader_waiter = None


class SPSCSender:
    """
    Single Producer sender for SPSC buffer.
    """

    def __init__(self, buffer: SPSCBuffer):
        self._buffer = buffer
        self._buffer._register_sender(self)

    def send(self, data: bytes) -> int:
        """
        Send data synchronously, returns number of bytes successfully written.
        """
        return self._buffer._push_bytes(data)

    def notify_reader(self):
        """Notify pending readers that there is data available."""
        self._buffer._notify_reader()

    async def wait_for_reader(self):
        """Wait for reader to consume data."""
        await self._buffer._wait_for_reader()

    async def send_async(self, data: bytes):
        """
        Send data asynchronously, will wait for space if needed.
        """
        remaining_data = data

        while remaining_data:
            # Try to send what we can
            written = self._buffer._push_bytes(remaining_data)

            if written > 0:
                # Alert pending readers that there is data available
                self._buffer._notify_reader()
                remaining_data = remaining_data[written:]

            if remaining_data:
                # Wait for reader to clear more space in the buffer
                await self._buffer._wait_for_reader()

    def into_sink(self) -> "SPSCSenderSink":
        """Convert this sender into a sink."""
        return SPSCSenderSink(self)


class SPSCReceiver:
    """
    Single Consumer receiver for SPSC buffer.
    """

    def __init__(self, buffer: SPSCBuffer):
        self._buffer = buffer
        self._buffer._register_receiver(self)

    def recv(self, size: int) -> bytearray:
        """
        Receive up to size bytes synchronously as bytearray copy.
        """
        # Get raw data from ring buffer
        raw_data = self._buffer._pop_bytes(size)
        if raw_data:
            self._buffer._notify_writer()

        # Return as bytearray copy
        return bytearray(raw_data)

    async def recv_into(self, out: bytearray) -> int:
        """
        Receive data into existing buffer asynchronously.
        Returns number of bytes copied.
        """
        copied = self._buffer._pop_into(out)
        if copied > 0:
            self._buffer._notify_writer()
        return copied

    async def wait_for(self, size: int):
        """
        Wait until at least size bytes are available.
        Prevents deadlock by capping at buffer capacity.
        """
        # Prevent deadlock by limiting to buffer capacity
        target_size = min(size, self._buffer._capacity)

        while self._buffer._size() < target_size:
            if not self._buffer._has_sender():
                raise error.SendersDisconnected
            await self._buffer._wait_for_writer()

    def into_stream(
        self, buffer_size: int = 8192, min_size: int = 1, pool_size: int = 10
    ) -> "SPSCReceiverStream":
        """Convert this receiver into a stream with managed buffer pool."""
        return SPSCReceiverStream(self, buffer_size, min_size, pool_size)


class SPSCSenderSink(Sink):
    """
    Sink implementation wrapping SPSCSender.
    """

    def __init__(self, sender: SPSCSender):
        self._sender = sender
        self._closed = False

    async def feed(self, item: bytes):
        if self._closed:
            raise error.SenderSinkClosed
        await self._sender.send_async(item)

    async def send(self, item: bytes):
        if self._closed:
            raise error.SenderSinkClosed
        await self._sender.send_async(item)

    async def flush(self):
        if self._closed:
            raise error.SenderSinkClosed
        # For SPSC buffer, flush is effectively a no-op since sends are immediate

    async def close(self):
        if not self._closed:
            del self._sender
            self._closed = True


class SPSCReceiverStream(Stream):
    """
    Stream implementation wrapping SPSCReceiver.
    Returns memoryview instances that reference managed buffers with automatic lifecycle.
    """

    def __init__(
        self,
        receiver: SPSCReceiver,
        buffer_size: int = 8192,
        min_size: int = 1,
        pool_size: int = 10,
    ):
        self._receiver = receiver
        self._min_size = min_size
        self._buffer_pool = BufferPool(buffer_size, pool_size)

    async def __anext__(self) -> memoryview:
        try:
            # Wait for minimum data
            await self._receiver.wait_for(self._min_size)

            # Get fresh buffer from pool
            managed_buffer = self._buffer_pool.get_buffer()

            # Read data into the buffer
            n_bytes = await self._receiver.recv_into(managed_buffer._buffer)

            if n_bytes == 0:
                raise StopAsyncIteration

            # Return memoryview slice - managed_buffer stays alive via closure
            return managed_buffer.view(0, n_bytes)

        except error.SendersDisconnected:
            raise StopAsyncIteration


class Channel(Generic[T]):
    """
    Internal Channel class managing the asyncio.Queue and tracking senders and receivers.
    """

    def __init__(self, maxsize: int | None):
        self.sync_queue: Deque[T] = deque([], maxlen=maxsize)
        self._senders = set()
        self._receivers = set()

        self._lock = threading.Lock()
        self._recv_waiters = deque([])
        self._send_waiters = deque([])

    def size(self):
        return len(self.sync_queue)

    def empty(self):
        return self.size() == 0

    def capacity(self):
        return self.sync_queue.maxlen or float("inf")

    def full(self):
        return self.size() == self.capacity()

    def register_sender(self, sender: "Sender[T]"):
        self._senders.add(weakref.ref(sender, self.sender_dropped))

    def register_receiver(self, receiver: "Receiver[T]"):
        self._receivers.add(weakref.ref(receiver, self.receiver_dropped))

    def has_receivers(self) -> bool:
        return len(self._receivers) > 0

    def has_senders(self) -> bool:
        return len(self._senders) > 0

    def sender_dropped(self, sender):
        self._senders.discard(sender)
        if not self.has_senders():
            notify_all(self._recv_waiters)

    def receiver_dropped(self, receiver):
        self._receivers.discard(receiver)
        if not self.has_receivers():
            notify_all(self._send_waiters)

    async def wait_for_receiver(self):
        await wait_for_notice(self._send_waiters)

    async def wait_for_sender(self):
        await wait_for_notice(self._recv_waiters)

    def notify_sender(self):
        notify_one(self._send_waiters)

    def notify_receiver(self):
        notify_one(self._recv_waiters)


class Sender(Generic[T]):
    """
    Sender class providing synchronous and asynchronous send methods.
    """

    def __init__(self, channel: Channel[T]):
        self._channel = channel
        self._channel.register_sender(self)

    async def send_async(self, item: T):
        """
        Asynchronously send an item to the channel and wait until it's processed.

        Args:
            item (Any): The item to send.

        Raises:
            ReceiversDisconnected: If no receivers exist or the channel is closed.
        """
        while True:
            if not self._channel.has_receivers():
                raise error.ReceiversDisconnected

            if not self._channel.full():
                self._channel.sync_queue.append(item)
                self._channel.notify_receiver()
                return

            # TODO: wait for receiver notification
            await self._channel.wait_for_receiver()

    def send(self, item: T):
        """
        Synchronously send an item to the channel.

        Args:
            item (Any): The item to send.

        Raises:
            ReceiversDisconnected: If no receivers exist or the channel is closed.
            ChannelFull: If the channel is bounded and full.
        """
        if not self._channel.has_receivers():
            raise error.ReceiversDisconnected

        if self._channel.full():
            raise error.ChannelFull

        self._channel.sync_queue.append(item)
        self._channel.notify_receiver()

    def into_sink(self) -> "SenderSink[T]":
        """
        Convert this Sender into a SenderSink.

        Returns:
            SenderSink: A Sink implementation wrapping this Sender.
        """
        return SenderSink(self)

    def __copy__(self):
        raise TypeError("Sender instances cannot be copied.")

    def __deepcopy__(self, memo):
        raise TypeError("Sender instances cannot be deep copied.")


class Receiver(Generic[T]):
    """
    Receiver class providing synchronous and asynchronous recv methods.
    """

    def __init__(self, channel: Channel[T]):
        self._channel = channel
        self._channel.register_receiver(self)

    async def recv(self) -> T:
        """
        Asynchronously receive an item from the channel.

        Returns:
            Any: The received item.

        Raises:
            SendersDisconnected: If no senders exist and the queue is empty.
        """

        while True:
            if not self._channel.empty():
                item = self._channel.sync_queue.popleft()
                self._channel.notify_sender()
                return item

            if not self._channel.has_senders():
                raise error.SendersDisconnected

            await self._channel.wait_for_sender()

    def into_stream(self) -> "ReceiverStream[T]":
        """
        Convert this Receiver into a ReceiverStream.

        Returns:
            ReceiverStream: A Stream implementation wrapping this Receiver.
        """
        return ReceiverStream(self)

    def __copy__(self):
        raise TypeError("Receiver instances cannot be copied.")

    def __deepcopy__(self, memo):
        raise TypeError("Receiver instances cannot be deep copied.")


class SenderSink(Sink, Generic[T]):
    """
    Sink implementation that wraps a Sender, allowing integration with Sink interfaces.
    """

    def __init__(self, sender: Sender[T]):
        self._sender = sender
        self._channel = sender._channel
        self._closed = False

    async def feed(self, item: T):
        if self._closed:
            raise error.SenderSinkClosed
        await self._sender.send_async(item)

    async def send(self, item: T):
        if self._closed:
            raise error.SenderSinkClosed
        await self._sender.send_async(item)

    async def flush(self):
        if self._closed:
            raise error.SenderSinkClosed

    async def close(self):
        if not self._closed:
            del self._sender
            self._closed = True


class ReceiverStream(Stream[T]):
    """
    Stream implementation that wraps a Receiver, allowing integration with Stream interfaces.
    """

    def __init__(self, receiver: Receiver[T]):
        self._receiver = receiver

    async def __anext__(self) -> T:
        try:
            return await self._receiver.recv()
        except error.SendersDisconnected:
            raise StopAsyncIteration


class OneShotChannel(asyncio.Future[T]):
    def sender_dropped(self):
        if not self.done():
            exception = error.SendersDisconnected
            self.set_exception(exception)


class OneShotSender(Generic[T]):
    def __init__(self, channel: OneShotChannel[T]):
        self._channel = channel
        weakref.finalize(self, channel.sender_dropped)

    def send(self, value: T):
        if self._channel.done():
            raise error.SenderExhausted("Value has already been sent on channel")

        loop = self._channel.get_loop()

        def setter():
            # NOTE: This code does not work if you dont schedule as a closure. WAT!
            self._channel.set_result(value)

        # The result must be set from the thread that owns the underlying future
        loop.call_soon_threadsafe(setter)


class OneShotReceiver(Generic[T]):
    def __init__(self, channel: OneShotChannel[T]):
        self._channel = channel

    async def __call__(self) -> T:
        return await self._channel


class WatchChannel(Generic[T]):
    def __init__(self, initial_value: T):
        # Tracks the version of the current value
        self._version = 0

        # Deque with maxlen=1 to store the current value
        self._queue: Deque[T] = deque([initial_value], maxlen=1)

        self._lock = threading.Lock()
        self._waiters: Deque[OneShotSender[None]] = deque()

        self._senders: weakref.WeakSet[WatchSender[T]] = weakref.WeakSet()
        self._receivers: weakref.WeakSet[WatchReceiver[T]] = weakref.WeakSet()

    def register_sender(self, sender: "WatchSender[T]"):
        """
        Register a new sender to the channel.
        """
        self._senders.add(sender)

    def register_receiver(self, receiver: "WatchReceiver[T]"):
        """
        Register a new receiver to the channel.
        """
        self._receivers.add(receiver)

    def has_senders(self) -> bool:
        """
        Check if there are any active receivers.
        """
        return len(self._senders) > 0

    def has_receivers(self) -> bool:
        """
        Check if there are any active receivers.
        """
        return len(self._receivers) > 0

    def get_current_value(self) -> T:
        """
        Retrieve the current value from the channel.
        """
        return self._queue[0]

    def notify(self):
        """
        Notify all receivers that a new value is available
        """
        notify_all(self._waiters)

    async def wait(self) -> None:
        # Create a oneshot channel
        channel = OneShotChannel()
        sender = OneShotSender(channel)
        receiver = OneShotReceiver(channel)

        # Register the sender
        self._waiters.append(sender)

        # wait for notification
        await receiver()

    def set_value(self, value: T):
        """
        Set a new value in the channel and increment the version.
        """
        with self._lock:
            self._queue.append(value)
            self._version += 1
            self.notify()


class WatchSender(Generic[T]):
    """
    Sender class providing methods to send and modify values in the watch channel.
    """

    def __init__(self, channel: WatchChannel[T]):
        self._channel = channel
        self._channel.register_sender(self)

    def subscribe(self) -> "WatchReceiver[T]":
        """
        Create a new receiver who is subscribed to this sender
        """
        return WatchReceiver(self._channel)

    def receiver_count(self) -> int:
        """
        Get the number of active receivers.
        """
        return len(self._channel._receivers)

    def send(self, value: T):
        """
        Asynchronously send a new value to the channel.

        Args:
            value (Any): The value to send.

        Raises:
            ReceiversDisconnected: if no receivers exist
        """
        if not self._channel.has_receivers():
            raise error.ReceiversDisconnected

        self._channel.set_value(value)

    def send_modify(self, func: Callable[[T], T]):
        """
        Modify the current value using a provided function and send the updated value.

        Args:
            func (Callable[[Any], Any]): Function to modify the current value.

        Raises:
            ReceiversDisconnected: if no receivers exist
        """
        if not self._channel.has_receivers():
            raise error.ReceiversDisconnected

        current = self._channel.get_current_value()
        new_value = func(current)
        self._channel.set_value(new_value)

    def send_if_modified(self, func: Callable[[T], T]):
        """
        Modify the current value using a provided function and send the updated value only if it has changed.

        Args:
            func (Callable[[Any], Any]): Function to modify the current value.

        Raises:
            ReceiversDisconnected: if no receivers exist
        """
        if not self._channel.has_receivers():
            raise error.ReceiversDisconnected

        current = self._channel.get_current_value()
        new_value = func(current)
        if new_value != current:
            self._channel.set_value(new_value)

    def borrow(self) -> T:
        """
        Borrow the current value without marking it as seen.

        Returns:
            Any: The current value.
        """
        return self._channel.get_current_value()


class WatchReceiver(Generic[T]):
    """
    Receiver class providing methods to access and await changes in the watch channel.
    """

    def __init__(self, channel: WatchChannel[T]):
        self._channel = channel
        self._last_version = channel._version  # Initialize with the current version
        self._channel.register_receiver(self)

    def borrow(self) -> T:
        """
        Borrow the current value without marking it as seen.

        Returns:
            Any: The current value.
        """
        return self._channel.get_current_value()

    def borrow_and_update(self) -> T:
        """
        Borrow the current value and mark it as seen.

        Returns:
            Any: The current value.
        """
        value = self._channel.get_current_value()
        self._last_version = self._channel._version
        return value

    async def changed(self):
        """
        Wait for the channel to have a new value that hasn't been seen yet.

        Raises:
            SendersDisconnected: If no senders exist
        """
        while True:
            with self._channel._lock:
                if self._channel._version > self._last_version:
                    # New value already available
                    self._last_version = self._channel._version
                    return

                if not self._channel.has_senders():
                    # Sender has been closed and no new values
                    raise error.SendersDisconnected

            # Note: We release the lock before waiting for notification. Otherwise we would deadlock
            # as senders would not be able to gain access to the underlying channel.
            await self._channel.wait()

    def into_stream(self) -> "WatchReceiverStream[T]":
        """
        Convert this WatchReceiver into a WatchReceiverStream.

        Returns:
            WatchReceiverStream: A Stream implementation wrapping this WatchReceiver.
        """
        return WatchReceiverStream(self)


async def _watch_stream(receiver: WatchReceiver[T]) -> AsyncIterator[T]:
    # Return the initial value in the watch
    yield receiver.borrow()

    # Otherwise only yield changes
    while True:
        try:
            await receiver.changed()
            yield receiver.borrow_and_update()
        except error.SendersDisconnected:
            break


class WatchReceiverStream(Stream[T]):
    """
    Stream implementation that wraps a WatchReceiver, allowing integration with Stream interfaces.
    """

    def __init__(self, receiver: WatchReceiver[T]):
        self._stream = _watch_stream(receiver)

    async def __anext__(self) -> T:
        return await anext(self._stream)
