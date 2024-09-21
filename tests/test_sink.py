import asyncio
import pytest

from typing import Any

from kioto import streams
from kioto.sink import Sink, drain

# Define a MockSink for testing purposes
class MockSink(Sink):
    def __init__(self):
        self.received_feed = []
        self.received_send = []
        self.flushed = False
        self.closed = False

    async def feed(self, item: Any):
        self.received_feed.append(item)

    async def send(self, item: Any):
        self.received_send.append(item)

    async def flush(self):
        self.flushed = True

    async def close(self):
        self.closed = True

@pytest.mark.asyncio
async def test_with_map():
    mock_sink = MockSink()
    transform_fn = lambda x: x.upper()

    # Use the fluent interface to create a With sink
    with_sink = mock_sink.with_map(transform_fn)

    # Send items
    await with_sink.feed("test")
    await with_sink.send("sink")

    # Flush and close
    await with_sink.flush()
    await with_sink.close()

    # Assertions
    assert mock_sink.received_feed == ["TEST"]
    assert mock_sink.received_send == ["SINK"]
    assert mock_sink.flushed is True
    assert mock_sink.closed is True

@pytest.mark.asyncio
async def test_buffer():
    mock_sink = MockSink()
    buffer_capacity = 2

    # Use the fluent interface to create a Buffer sink
    buffer_sink = mock_sink.buffer(capacity=buffer_capacity)

    # Send items
    await buffer_sink.feed("Item 1")
    await buffer_sink.feed("Item 2")
    await buffer_sink.feed("Item 3")  # This should wait until there's space

    # At this point, "Item 1" and "Item 2" should have been sent to the mock_sink
    assert mock_sink.received_feed == ["Item 1", "Item 2"]

    # Allow some time for the worker to process
    await asyncio.sleep(0.1)

    # Flush and close
    await buffer_sink.flush()
    await buffer_sink.close()

    # Assertions after flushing
    assert mock_sink.received_feed == ["Item 1", "Item 2", "Item 3"]
    assert mock_sink.flushed is True
    assert mock_sink.closed is True

@pytest.mark.asyncio
async def test_fanout():
    mock_sink1 = MockSink()
    mock_sink2 = MockSink()

    # Use the fluent interface to create a Fanout sink
    fanout_sink = mock_sink1.fanout(mock_sink2)

    # Send items
    await fanout_sink.feed("Fanout Item 1")
    await fanout_sink.send("Fanout Item 2")

    # Flush and close
    await fanout_sink.flush()
    await fanout_sink.close()

    # Assertions for both sinks
    assert mock_sink1.received_feed == ["Fanout Item 1"]
    assert mock_sink1.received_send == ["Fanout Item 2"]
    assert mock_sink1.flushed is True
    assert mock_sink1.closed is True

    assert mock_sink2.received_feed == ["Fanout Item 1"]
    assert mock_sink2.received_send == ["Fanout Item 2"]
    assert mock_sink2.flushed is True
    assert mock_sink2.closed is True

@pytest.mark.asyncio
async def test_send_all():
    mock_sink = MockSink()
    transform_fn = lambda x: x * 2  # Example transformation

    # Use the fluent interface to create a With sink
    with_sink = mock_sink.with_map(transform_fn)

    # Create a stream with multiple items
    test_stream = streams.iter([1, 2, 3, 4])

    # Use send_all to send all items from the stream
    await with_sink.send_all(test_stream)

    # Close the sink
    await with_sink.close()

    # Assertions
    assert mock_sink.received_feed == [2, 4, 6, 8]
    assert mock_sink.flushed is True
    assert mock_sink.closed is True

@pytest.mark.asyncio
async def test_buffer_with_stream():
    mock_sink = MockSink()
    buffer_capacity = 3

    # Use the fluent interface to create a Buffer sink
    buffer_sink = mock_sink.buffer(capacity=buffer_capacity)

    # Create a stream with multiple items
    test_stream = streams.iter(['a', 'b', 'c', 'd', 'e'])

    # Use send_all to send all items from the stream
    await buffer_sink.send_all(test_stream)

    # Close the sink
    await buffer_sink.close()

    # Assertions
    assert mock_sink.received_feed == ['a', 'b', 'c', 'd', 'e']
    assert mock_sink.flushed is True
    assert mock_sink.closed is True

@pytest.mark.asyncio
async def test_fanout_with_multiple_sinks():
    mock_sink1 = MockSink()
    mock_sink2 = MockSink()
    mock_sink3 = MockSink()

    # Use the fluent interface to create a Fanout sink with multiple sinks
    fanout_sink = mock_sink1.fanout(mock_sink2).fanout(mock_sink3)

    # Create a stream with multiple items
    test_stream = streams.iter(['x', 'y', 'z'])

    # Use send_all to send all items from the stream
    await fanout_sink.send_all(test_stream)

    # Close the sink
    await fanout_sink.close()

    # Assertions for all sinks
    expected = ['x', 'y', 'z']
    for mock_sink in [mock_sink1, mock_sink2, mock_sink3]:
        assert mock_sink.received_feed == expected
        assert mock_sink.flushed is True
        assert mock_sink.closed is True