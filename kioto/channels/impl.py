import asyncio
import weakref
from typing import Any

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
        self._chan = channel

        # Register sender and finalizer
        self._chan.register_sender(self)
        self._finalizer = weakref.finalize(self, self._close)

    def _close(self):
        if hasattr(self, "_chan"):
            # Deregister ourselves from the channel
            self._chan._senders.discard(self)
            # Drop our reference to the channel
            del self._chan

    @property
    def _channel(self):
        try:
            return self._chan
        except AttributeError:
            raise RuntimeError("Channel is closed!")

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
        self._chan = channel

        # Register receiver and finalizer
        self._finalizer = weakref.finalize(self, self._close)
        self._channel.register_receiver(self)

    def _close(self):
        """
        Synchronously close the receiver.
        """
        if hasattr(self, "_chan"):
            self._chan._receivers.discard(self)
            del self._chan

    @property
    def _channel(self):
        try:
            return self._chan
        except AttributeError:
            raise RuntimeError("Channel is closed!")

    async def recv(self) -> Any:
        """
        Asynchronously receive an item from the channel.

        Returns:
            Any: The received item.

        Raises:
            RuntimeError: If no senders exist and the queue is empty.
        """
        if self._channel.empty() and not self._channel.has_senders():
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
        await self._sender._channel.sync_queue.join()

    async def close(self):
        if not self._closed:
            self._closed = True
            self._sender._close()


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