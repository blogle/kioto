# Kioto
![GitHub Actions Workflow Status](https://img.shields.io/github/actions/workflow/status/blogle/kioto/ci.yml)
![Codecov](https://img.shields.io/codecov/c/github/blogle/kioto)
![PyPI - Version](https://img.shields.io/pypi/v/kioto)

**Kioto** is an asynchronous utilities library for Python, inspired by [Tokio](https://tokio.rs/) from Rust. Leveraging Python's `asyncio`, Kioto provides a suite of powerful async utilities and data structures, enabling developers to build efficient and scalable asynchronous applications with ease.

## Features

**Asynchronous Streams (kioto.streams)**

- *Streams*: The library provides an abstraction over asynchronous data streams, allowing you to work with sequences of data that are produced asynchronously.
- *Stream Transformations*: It includes methods like map, filter, flat_map, chunks, zip, etc., enabling functional-style transformations on streams.
- *Stream Creation*: Functions like iter, once, repeat, and repeat_with help in creating streams from various sources.
- *Stream Composition*: The chain method allows for sequential composition of streams, and select enables racing multiple streams and processing their outputs as they become available.

**Asynchronous Channels (kioto.channels):**

- *Channels:* They provide a way for different parts of your application to communicate asynchronously by sending messages to each other.
- *Bounded and Unbounded Channels:* You can create channels with or without capacity limits, controlling backpressure and flow of data.
- *One-shot Channels:* For sending a single message between tasks.
- *Watch Channels:* Allow tasks to watch a value and get notified when it changes.
- *Channel Integration:* Channels can be converted into streams (into_stream) and sinks (into_sink), enabling seamless integration with other parts of the library.

**Futures and Task Utilities (kioto.futures):**

- *Futures:* Abstractions over asynchronous computations that will produce a result in the future.
- *Task Sets:* Group multiple asynchronous tasks and await their completion, either individually or collectively.
- *Selection and Racing:* The select function allows you to wait for the first task to complete among a set, enabling patterns like racing tasks.
- *Shared Futures:* The shared function lets multiple tasks await the same future, ensuring the computation is only performed once.
- *Lazy Evaluation:* The lazy function defers computation until the result is needed.

**Sinks (kioto.sink):**

- *Sinks:* Represent consumers of data streams. They receive data and process it, potentially asynchronously.
- *Sink Transformations:* Methods like with_map, buffer, and fanout allow you to modify or control the flow of data into the sink.
- *File Sinks:* A concrete implementation that writes streamed data to files asynchronously.

**Time Utilities (kioto.time):**

- *Intervals and Timers:* Functions like interval and interval_at help in scheduling tasks to run at specific intervals.
- *Timeouts:* Functions like timeout and timeout_at allow you to impose time limits on asynchronous operations.
- *Sleeping:* The sleep_until function enables tasks to pause execution until a specific time.

**Synchronization Primitives (kioto.sync):**

- *Mutex:* An asynchronous mutex for synchronizing access to shared resources across tasks, ensuring data integrity without blocking the event loop.

## Usage Examples

Here are some example programs demonstrating how to use Kioto's features:

### Async Channels
Kioto provides channnels with a sender/receiver pair. If one end of the channel isgc'd the other end will raise an exception on send/recv.

```python
import asyncio
from kioto.channels import channel

async def producer(sender):
    for i in range(5):
        await sender.send_async(i)
        print(f"Sent: {i}")

async def consumer(receiver):
    # recv will raise an exception, once producer loop
    # finishes and the sender goes out of scope.
    while item := await receiver.recv()
        print(f"Received: {item}")

def pipeline():
    sender, receiver = channel(10)
    return asyncio.gather(producer(sender), consumer(receiver))

async def main():
    await pipeline()
```

### Select on Task Completion

Use Kioto's task management utilities to await the completion of multiple asynchronous tasks and handle their results using a `match` statement.

```python
import asyncio
from kioto.futures import task_set, select

async def fetch_data():
    await asyncio.sleep(1)
    return "Data fetched"

async def process_data():
    await asyncio.sleep(2)
    return "Data processed"

async def main():
    tasks = task_set(fetch=fetch_data(), process=process_data())
    while tasks:
        match await select(tasks):
            case "fetch", result:
                print(f"fetched: {result}")
                # Dispatch or handle the fetched data
            case "process", result:
                print(f"processed: {result}")
                # Dispatch or handle the processed data
```

### Mutex with owned contents
Kioto includes syncronization primitives that own their contents.

```python
import asyncio
from kioto.futures import try_join
from kioto.sync import Mutex

class Counter:
    def __init__(self):
        self.value = 0

async def increment(mutex: Mutex, times: int):
	# The guard is only valid in the context manager.
	# It will raise an exception if a reference outlives this scope
    async with mutex.lock() as guard:
        for _ in range(times):
            guard.value += 1
            await asyncio.sleep(0.1)

async def main():
    mutex = Mutex(Counter)
    await try_join(
        increment(mutex, 5),
        increment(mutex, 5)
    )
    
    print(f"Final counter value: {counter.value}")
```

### Stream Combinators
Use stream combinators to implement complex data pipelines.

``` python
import asyncio
from kioto import streams

async def main():
    stream = (
        streams.iter(range(10))
            .filter(lambda x: x % 2 == 0)
            .map(lambda x: x * 2)
    )

    # Iterate the stream.
    async for item in stream:
        print(item)

    # Alternatively collect into a list
    values = await stream.collect()
```

Implement the Stream class by decorating your async generators
```python

import asyncio
from kioto import streams

@streams.async_stream
async def sock_stream(sock):
    while result := await sock.recv(1024):
        yield result

# Read urls off the socket and download them 10 at a time
downloads = await (
    sock_stream(socket)
      .map(lambda url: request.get(url))
      .buffered_unordered(10)
      .collect()
)
```


# License
Kioto is released under the MIT License

<hr>
Feel free to contribute to Kioto by submitting issues or pull requests on GitHub. For more detailed documentation, visit the official documentation site.
