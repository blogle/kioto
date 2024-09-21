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