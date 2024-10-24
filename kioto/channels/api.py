import asyncio
from kioto.channels import impl
from typing import Any

def channel(capacity: int) -> tuple[impl.Sender, impl.Receiver]:
    channel = impl.Channel(capacity)
    sender = impl.Sender(channel)
    receiver = impl.Receiver(channel)
    return sender, receiver

def channel_unbounded() -> tuple[impl.Sender, impl.Receiver]:
    channel = impl.Channel(0)
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
