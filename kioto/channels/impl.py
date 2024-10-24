import asyncio
import threading
import weakref

from collections import deque
from typing import Any, Callable

from kioto.streams import Stream
from kioto.sink import Sink

class Channel:
    """
    Internal Channel class managing the asyncio.Queue and tracking senders and receivers.
    """
    def __init__(self, maxsize: int):
        self.sync_queue = asyncio.Queue(maxsize=maxsize)
        self._senders = weakref.WeakSet()
        self._receivers = weakref.WeakSet()

    def register_sender(self, sender: 'Sender'):
        self._senders.add(sender)

    def register_receiver(self, receiver: 'Receiver'):
        self._receivers.add(receiver)

    def has_receivers(self) -> bool:
        return len(self._receivers) > 0

    def has_senders(self) -> bool:
        return len(self._senders) > 0

    def empty(self):
        return self.sync_queue.empty()


class Sender:
    """
    Sender class providing synchronous and asynchronous send methods.
    """
    def __init__(self, channel: Channel):
        self._channel = channel
        self._channel.register_sender(self)

    async def send_async(self, item: Any):
        """
        Asynchronously send an item to the channel and wait until it's processed.

        Args:
            item (Any): The item to send.

        Raises:
            RuntimeError: If no receivers exist or the channel is closed.
        """
        if not self._channel.has_receivers():
            raise RuntimeError("No receivers exist. Cannot send.")
        await self._channel.sync_queue.put(item)

    def send(self, item: Any):
        """
        Synchronously send an item to the channel.

        Args:
            item (Any): The item to send.

        Raises:
            RuntimeError: If no receivers exist or the channel is closed.
            asyncio.QueueFull: If the channel is bounded and full.
        """
        if not self._channel.has_receivers():
            raise RuntimeError("No receivers exist. Cannot send.")
        self._channel.sync_queue.put_nowait(item)

    def into_sink(self) -> 'SenderSink':
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


class Receiver:
    """
    Receiver class providing synchronous and asynchronous recv methods.
    """
    def __init__(self, channel: Channel):
        self._channel = channel
        self._channel.register_receiver(self)

    async def recv(self) -> Any:
        """
        Asynchronously receive an item from the channel.

        Returns:
            Any: The received item.

        Raises:
            RuntimeError: If no senders exist and the queue is empty.
        """

        # If there is data in the queue, then we can immediately read it
        if not self._channel.empty():
            self._channel.sync_queue.task_done()
            return self._channel.sync_queue.get_nowait()

        if not self._channel.has_senders():
            raise RuntimeError("No senders exist. Cannot receive.")

        item = await self._channel.sync_queue.get()
        self._channel.sync_queue.task_done()
        return item

    def into_stream(self) -> 'ReceiverStream':
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


class SenderSink(Sink):
    """
    Sink implementation that wraps a Sender, allowing integration with Sink interfaces.
    """
    def __init__(self, sender: Sender):
        self._sender = sender
        self._channel = sender._channel
        self._closed = False

    async def feed(self, item: Any):
        if self._closed:
            raise RuntimeError("Cannot feed to a closed Sink.")
        await self._sender.send_async(item)

    async def send(self, item: Any):
        if self._closed:
            raise RuntimeError("Cannot send to a closed Sink.")
        await self._sender.send_async(item)
        await self.flush()

    async def flush(self):
        if self._closed:
            raise RuntimeError("Cannot flush a closed Sink.")
        await self._channel.sync_queue.join()

    async def close(self):
        if not self._closed:
            del self._sender
            await self.flush()
            self._closed = True


class ReceiverStream(Stream):
    """
    Stream implementation that wraps a Receiver, allowing integration with Stream interfaces.
    """
    def __init__(self, receiver: Receiver):
        self._receiver = receiver

    async def __anext__(self):
        try:
            return await self._receiver.recv()
        except Exception:
            raise StopAsyncIteration

class OneShotChannel(asyncio.Future):

    def sender_dropped(self):
        if not self.done():
            exception = RuntimeError("Sender was dropped")
            self.set_exception(exception)

class OneShotSender:

    def __init__(self, channel):
        self._channel = channel
        weakref.finalize(self, channel.sender_dropped)

    def send(self, value):
        if self._channel.done():
            raise RuntimeError("Value has already been sent on channel")

        self._channel.set_result(value)

class OneShotReceiver:
    def __init__(self, channel):
        self._channel = channel

    async def __call__(self):
        return await self._channel


class WatchChannel:

    def __init__(self, initial_value: Any):
        # Tracks the version of the current value
        self._version = 0

        # Deque with maxlen=1 to store the current value
        self._queue = deque([initial_value], maxlen=1)

        self._lock = threading.Lock()
        self._waiters = deque()

        self._senders = weakref.WeakSet()
        self._receivers = weakref.WeakSet()

    def register_sender(self, sender: 'WatchSender'):
        """
        Register a new sender to the channel.
        """
        self._senders.add(sender)

    def register_receiver(self, receiver: 'WatchReceiver'):
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

    def get_current_value(self) -> Any:
        """
        Retrieve the current value from the channel.
        """
        return self._queue[0]

    def notify(self):
        """
        Notify all receivers that a new value is available
        """
        for tx in self._waiters:
            tx.send(())

    async def wait(self):
        # Create a oneshot channel
        channel = OneShotChannel()
        sender = OneShotSender(channel)
        receiver = OneShotReceiver(channel)

        # Register the sender
        self._waiters.append(sender)

        # wait for notification
        try:
            await receiver()
        finally:
            self._waiters.remove(sender)


    def set_value(self, value: Any):
        """
        Set a new value in the channel and increment the version.
        """
        with self._lock:
            self._queue.append(value)
            self._version += 1
            self.notify()


class WatchSender:
    """
    Sender class providing methods to send and modify values in the watch channel.
    """
    def __init__(self, channel: WatchChannel):
        self._channel = channel
        self._channel.register_sender(self)

    def subscribe(self) -> 'WatchReceiver':
        """
        Create a new receiver who is subscribed to this sender
        """
        return WatchReceiver(self._channel)

    def receiver_count(self) -> int:
        """
        Get the number of active receivers.
        """
        return len(self._channel._receivers)

    def send(self, value: Any):
        """
        Asynchronously send a new value to the channel.

        Args:
            value (Any): The value to send.

        Raises:
            RuntimeError: If the sender is closed or no receivers exist.
        """
        if not self._channel.has_receivers():
            raise RuntimeError("No receivers exist. Cannot send.")

        self._channel.set_value(value)

    def send_modify(self, func: Callable[[Any], Any]):
        """
        Modify the current value using a provided function and send the updated value.

        Args:
            func (Callable[[Any], Any]): Function to modify the current value.

        Raises:
            RuntimeError: If the sender is closed or no receivers exist.
        """
        if not self._channel.has_receivers():
            raise RuntimeError("No receivers exist. Cannot send.")

        current = self._channel.get_current_value()
        new_value = func(current)
        self._channel.set_value(new_value)

    def send_if_modified(self, func: Callable[[Any], Any]):
        """
        Modify the current value using a provided function and send the updated value only if it has changed.

        Args:
            func (Callable[[Any], Any]): Function to modify the current value.

        Raises:
            RuntimeError: If the sender is closed or no receivers exist.
        """
        if not self._channel.has_receivers():
            raise RuntimeError("No receivers exist. Cannot send.")

        current = self._channel.get_current_value()
        new_value = func(current)
        if new_value != current:
            self._channel.set_value(new_value)

    def borrow(self) -> Any:
        """
        Borrow the current value without marking it as seen.

        Returns:
            Any: The current value.
        """
        return self._channel.get_current_value()

class WatchReceiver:
    """
    Receiver class providing methods to access and await changes in the watch channel.
    """
    def __init__(self, channel: WatchChannel):
        self._channel = channel
        self._last_version = channel._version  # Initialize with the current version
        self._channel.register_receiver(self)

    def borrow(self) -> Any:
        """
        Borrow the current value without marking it as seen.

        Returns:
            Any: The current value.
        """
        return self._channel.get_current_value()

    def borrow_and_update(self) -> Any:
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
            RuntimeError: If the sender has been closed and no new values are available.
        """
        while True:
            with self._channel._lock:

                if self._channel._version > self._last_version:
                    # New value already available
                    self._last_version = self._channel._version
                    return

                if not self._channel.has_senders():
                    # Sender has been closed and no new values
                    raise RuntimeError("Sender has been closed and no new values are available.")

            # Note: We release the lock before waiting for notification. Otherwise we would deadlock
            # as senders would not be able to gain access to the underlying channel.
            await self._channel.wait()

    def into_stream(self) -> 'WatchReceiverStream':
        """
        Convert this WatchReceiver into a WatchReceiverStream.

        Returns:
            WatchReceiverStream: A Stream implementation wrapping this WatchReceiver.
        """
        return WatchReceiverStream(self)

async def _watch_stream(receiver):
    # Return the initial value in the watch
    yield receiver.borrow()

    # Otherwise only yield changes
    while True:
        try:
            await receiver.changed()
            yield receiver.borrow_and_update()
        except RuntimeError as e:
            break


class WatchReceiverStream(Stream):
    """
    Stream implementation that wraps a WatchReceiver, allowing integration with Stream interfaces.
    """
    def __init__(self, receiver: Receiver):
        self._stream = _watch_stream(receiver)

    async def __aiter__(self):
        return self._stream

    async def __anext__(self):
        return await anext(self._stream)

