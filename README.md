# Kioto
![GitHub Actions Workflow Status](https://img.shields.io/github/actions/workflow/status/blogle/kioto/ci.yml)
![Codecov](https://img.shields.io/codecov/c/github/blogle/kioto)
![PyPI - Version](https://img.shields.io/pypi/v/kioto)

**Kioto** is an asynchronous utilities library for Python, inspired by [Tokio](https://tokio.rs/) from Rust. Leveraging Python's `asyncio`, Kioto provides a suite of powerful async utilities and data structures, enabling developers to build efficient and scalable asynchronous applications with ease.

## Features

- **Async Channels:** Facilitate communication between asynchronous tasks with bounded and unbounded channels.
- **Futures and Task Management:** Simplify asynchronous task handling with utilities for managing task sets, selection, and shared futures.
- **Streams and Sinks:** Provide stream processing capabilities, including mapping, filtering, buffering, and more.
- **Synchronization Primitives:** Offer advanced synchronization tools like mutexes for managing shared state.
- **Time Utilities:** Handle asynchronous timing operations, intervals, and timeouts seamlessly.

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
