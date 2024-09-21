import asyncio
from kioto.channels import impl

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
    channel = asyncio.Future()

    class OneShotSender:
        def __init__(self):
            self._sent = False

        def send(self, value):
            if self._sent:
                raise RuntimeError("Value has already been sent on channel")

            channel.set_result(value)
            self._sent = True

    async def receiver():
        return await channel

    return OneShotSender(), receiver()
