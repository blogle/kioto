from kioto.channels import impl
from typing import Any


def channel(capacity: int) -> tuple[impl.Sender, impl.Receiver]:
    channel = impl.Channel(capacity)
    sender = impl.Sender(channel)
    receiver = impl.Receiver(channel)
    return sender, receiver


def channel_unbounded() -> tuple[impl.Sender, impl.Receiver]:
    channel = impl.Channel(None)
    sender = impl.Sender(channel)
    receiver = impl.Receiver(channel)
    return sender, receiver


def oneshot_channel():
    channel = impl.OneShotChannel()
    sender = impl.OneShotSender(channel)
    receiver = impl.OneShotReceiver(channel)
    return sender, receiver()


def watch(initial_value: Any) -> tuple[impl.WatchSender, impl.WatchReceiver]:
    channel = impl.WatchChannel(initial_value)
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
