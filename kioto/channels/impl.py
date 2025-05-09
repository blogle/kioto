import asyncio
import threading
import weakref

from collections import deque
from typing import Any, Callable

from kioto.streams import Stream
from kioto.sink import Sink

from . import error


def notify_one(waiters):
    if waiters:
        tx = waiters.pop()
        tx.send(())


def notify_all(waiters):
    while waiters:
        tx = waiters.pop()
        if not tx._channel.done():
            tx.send(())


def wait_for_notice(waiters):
    # Create a oneshot channel
    channel = OneShotChannel()
    sender = OneShotSender(channel)
    receiver = OneShotReceiver(channel)

    # register the tx side for notification
    waiters.append(sender)

    return receiver()


class Channel:
    """
    Internal Channel class managing the asyncio.Queue and tracking senders and receivers.
    """

    def __init__(self, maxsize: int | None):
        self.sync_queue = deque([], maxlen=maxsize)
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

    def register_sender(self, sender: "Sender"):
        self._senders.add(weakref.ref(sender, self.sender_dropped))

    def register_receiver(self, receiver: "Receiver"):
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

    def send(self, item: Any):
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

    def into_sink(self) -> "SenderSink":
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

    def into_stream(self) -> "ReceiverStream":
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
            raise error.SenderSinkClosed
        await self._sender.send_async(item)

    async def send(self, item: Any):
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


class ReceiverStream(Stream):
    """
    Stream implementation that wraps a Receiver, allowing integration with Stream interfaces.
    """

    def __init__(self, receiver: Receiver):
        self._receiver = receiver

    async def __anext__(self):
        try:
            return await self._receiver.recv()
        except error.SendersDisconnected:
            raise StopAsyncIteration


class OneShotChannel(asyncio.Future):
    def sender_dropped(self):
        if not self.done():
            exception = error.SendersDisconnected
            self.set_exception(exception)


class OneShotSender:
    def __init__(self, channel):
        self._channel = channel
        weakref.finalize(self, channel.sender_dropped)

    def send(self, value):
        if self._channel.done():
            raise error.SenderExhausted("Value has already been sent on channel")

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

    def register_sender(self, sender: "WatchSender"):
        """
        Register a new sender to the channel.
        """
        self._senders.add(sender)

    def register_receiver(self, receiver: "WatchReceiver"):
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
        notify_all(self._waiters)

    async def wait(self):
        # Create a oneshot channel
        channel = OneShotChannel()
        sender = OneShotSender(channel)
        receiver = OneShotReceiver(channel)

        # Register the sender
        self._waiters.append(sender)

        # wait for notification
        await receiver()

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

    def subscribe(self) -> "WatchReceiver":
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
            ReceiversDisconnected: if no receivers exist
        """
        if not self._channel.has_receivers():
            raise error.ReceiversDisconnected

        self._channel.set_value(value)

    def send_modify(self, func: Callable[[Any], Any]):
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

    def send_if_modified(self, func: Callable[[Any], Any]):
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

    def into_stream(self) -> "WatchReceiverStream":
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
        except error.SendersDisconnected:
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
