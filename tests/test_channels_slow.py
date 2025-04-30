import asyncio
import itertools
import random
import pytest

from kioto.channels import channel, error
from kioto.futures import try_join 

items = [10, 100, 10_000]
workers = [1, 2, 5, 10]

items = [15]
workers = [3]

@pytest.mark.slow
@pytest.mark.asyncio
@pytest.mark.parametrize("n, n_workers", itertools.product(items, workers))
async def test_channel_fan_out_in(n, n_workers):
    results = set()

    def tasks():
        tx_1, rx_1 = channel(10)
        tx_2, rx_2 = channel(10)

        async def source():
            await asyncio.sleep(random.random())
            for i in range(n):
                await tx_1.send_async(i)

        async def worker():
            await asyncio.sleep(random.random())
            while True:
                try:
                    val = await rx_1.recv()
                except error.SendersDisconnected:
                    break
                
                await tx_2.send_async(val)

        async def sink():
            await asyncio.sleep(random.random())
            while True:
                try:
                    val = await rx_2.recv()
                except error.SendersDisconnected:
                    break
                results.add(val)


        workers = [worker() for _ in range(n_workers)] 
        return try_join(source(), sink(), *workers)


    await tasks()
    assert set(range(n)) == results
        

@pytest.mark.slow
@pytest.mark.asyncio
@pytest.mark.parametrize("n, n_workers", itertools.product(items, workers))
async def test_channel_fan_out_in_cancel(n, n_workers):
    results = set()

    def tasks():

        tx_1, rx_1 = channel(10)
        tx_2, rx_2 = channel(10)

        async def source():
            nonlocal tx_1
            await asyncio.sleep(random.random())
            for i in range(n):
                await tx_1.send_async(i)
            print("source done")

            del tx_1

        async def worker():
            await asyncio.sleep(random.random())
            while True:
                try:
                    print("fetching rx_1")
                    val = await rx_1.recv()
                except error.SendersDisconnected:
                    break
                except asyncio.CancelledError:
                    return
                else:
                    await tx_2.send_async(val)
            print("worker done")

        async def sink():
            await asyncio.sleep(random.random())
            while True:
                try:
                    #print("fetching rx_2")
                    val = await rx_2.recv()
                    print(val)
                    results.add(val)
                except error.SendersDisconnected:
                    break
            print("sink done")

        return source, worker, sink

    n_cancel = 1
    source, worker, sink = tasks()
    workers = [worker() for _ in range(n_workers - n_cancel)] 

    kill_workers = []
    async with asyncio.TaskGroup() as tg:
        healthy = tg.create_task(try_join(source(), sink(), *workers))
        for _ in range(n_cancel):
            task = tg.create_task(worker())
            kill_workers.append(task)

        await asyncio.sleep(random.random())
        for task in kill_workers:
            task.cancel()
            await task


    assert set(range(n)) == results
