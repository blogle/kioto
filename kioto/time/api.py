import asyncio
import time

from kioto.time import impl

# Re-export the public API
MissedTickBehavior = impl.MissedTickBehavior

def instant():
    return impl.Instant()

def interval(period, missed_tick_behavior=MissedTickBehavior.Burst):
    return impl.Interval(period, missed_tick_behavior=missed_tick_behavior)

def interval_at(start, period, missed_tick_behavior=MissedTickBehavior.Burst):
    return impl.Interval(period, start=start, missed_tick_behavior=missed_tick_behavior)

async def sleep_until(deadline):
    await asyncio.sleep(deadline - time.monotonic())

async def timeout(duration, coro):
    async with asyncio.timeout(duration):
        return await coro

async def timeout_at(deadline, coro):
    duration = deadline - time.monotonic()
    return await timeout(duration, coro)
