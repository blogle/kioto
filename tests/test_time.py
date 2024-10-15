import asyncio
import pytest
import time

from kioto.time import instant, interval, interval_at, sleep_until, timeout, timeout_at, MissedTickBehavior

TOL = 5e-3
def approx(a):
    return pytest.approx(a, abs=TOL)

@pytest.mark.xfail(reason="known issue with the initial interval")
@pytest.mark.asyncio
async def test_interval():
    duration = 0.1
    now = instant()
    timer = interval(duration)

    now = instant()
    await timer.tick()
    assert now.elapsed() == approx(duration)

@pytest.mark.asyncio
async def test_interval_at_w_burst():
    duration = 0.1

    # Simulate a delay of 1 second
    past = time.monotonic() - 1
    timer = interval_at(past, duration, missed_tick_behavior=MissedTickBehavior.Burst)

    # The next 10 ticks should return immediately
    for _ in range(10):
        now = instant()
        await timer.tick()
        assert now.elapsed() == approx(0.0)

    # The next tick should be after 0.1 seconds
    now = instant()
    await timer.tick()
    assert now.elapsed() == approx(duration)

@pytest.mark.asyncio
async def test_interval_at_w_delay():
    duration = .1

    # Simulate a delay of 1 second
    start = time.monotonic()
    past = start - 1
    timer = interval_at(past, duration, missed_tick_behavior=MissedTickBehavior.Delay)

    # The next tick should return immediately
    now = instant()
    await timer.tick()
    assert now.elapsed() == approx(0.0)

    # All future ticks should finish in after the period duration
    for i in range(10):
        now = instant()
        await timer.tick()
        assert now.elapsed() == approx(duration)

@pytest.mark.asyncio
async def test_interval_at_w_skip():
    duration = .1

    # Simulate a delay of 1 second
    past = time.monotonic() - 1
    timer = interval_at(past, duration, missed_tick_behavior=MissedTickBehavior.Skip)

    # The next tick should return immediately
    now = instant()
    await timer.tick()
    assert now.elapsed() == approx(0.0)

    # Future ticks should be some multiple of the period from the initial start
    for tick in range(10):
        clock = instant()
        await timer.tick()
        assert (time.monotonic() - past) % duration == approx(0.0)

@pytest.mark.asyncio
async def test_sleep_at():
    start = time.monotonic()
    duration = 0.1

    now = instant()
    await sleep_until(start + duration)
    assert now.elapsed() == approx(duration)

@pytest.mark.asyncio
async def test_timeout():
    duration = 0.1

    async def task(idle):
        await asyncio.sleep(idle)
        return 1

    with pytest.raises(asyncio.TimeoutError):
        await timeout(duration, task(0.2))

    assert await timeout(duration, task(0)) == 1


@pytest.mark.asyncio
async def test_timeout_at():
    duration = 0.1

    async def task(idle):
        await asyncio.sleep(idle)
        return 1

    with pytest.raises(asyncio.TimeoutError):
        await timeout_at(time.monotonic() + duration, task(0.2))

    assert await timeout(time.monotonic() + duration, task(0)) == 1
