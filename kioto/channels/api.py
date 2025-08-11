from __future__ import annotations

from typing import Awaitable, TypeVar

from kioto.channels import impl

T = TypeVar("T")


def channel(capacity: int) -> tuple[impl.Sender[T], impl.Receiver[T]]:
    channel: impl.Channel[T] = impl.Channel(capacity)
    sender = impl.Sender(channel)
    receiver = impl.Receiver(channel)
    return sender, receiver


def channel_unbounded() -> tuple[impl.Sender[T], impl.Receiver[T]]:
    channel: impl.Channel[T] = impl.Channel(None)
    sender = impl.Sender(channel)
    receiver = impl.Receiver(channel)
    return sender, receiver


def oneshot_channel() -> tuple[impl.OneShotSender[T], Awaitable[T]]:
    channel: impl.OneShotChannel[T] = impl.OneShotChannel()
    sender = impl.OneShotSender(channel)
    receiver = impl.OneShotReceiver(channel)
    return sender, receiver()


def watch(initial_value: T) -> tuple[impl.WatchSender[T], impl.WatchReceiver[T]]:
    channel: impl.WatchChannel[T] = impl.WatchChannel(initial_value)
    sender = impl.WatchSender(channel)
    receiver = impl.WatchReceiver(channel)
    return sender, receiver


def spsc_buffer(capacity: int) -> tuple[impl.SPSCSender, impl.SPSCReceiver]:
    """
    Create a Single Producer Single Consumer buffer for bytes.

    Args:
        capacity: Buffer capacity in bytes (will be rounded up to nearest power of 2)

    Returns:
        A tuple of (sender, receiver) for the SPSC buffer
    """
    buffer = impl.SPSCBuffer(capacity)
    sender = impl.SPSCSender(buffer)
    receiver = impl.SPSCReceiver(buffer)
    return sender, receiver
