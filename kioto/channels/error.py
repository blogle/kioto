class ChannelFull(Exception):
    pass


class ChannelEmpty(Exception):
    pass


class SendersDisconnected(Exception):
    pass


class ReceiversDisconnected(Exception):
    pass


class SenderSinkClosed(Exception):
    pass


class SenderExhausted(Exception):
    pass


class ReceiverExhausted(Exception):
    pass
