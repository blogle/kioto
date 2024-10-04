import asyncio
import time

from enum import Enum

class Instant:
    def __init__(self):
        self.start = time.monotonic()

    def elapsed(self):
        return time.monotonic() - self.start

    def restart(self):
        self.start = time.monotonic()

class MissedTickBehavior(Enum):
    Burst = 0
    Delay = 1
    Skip = 2

class Interval:

    def __init__(self, period, start=time.monotonic(), missed_tick_behavior=MissedTickBehavior.Burst):
        self.start = start
        self.period = period
        self.ticks = 0
        self.missed_tick_behavior = missed_tick_behavior

    def reset(self):
        """
        Reset the interval to complete one period after the current time.
        This is equivalent to calling reset_at(time.monotonic() + period).
        """
        self.reset_at(time.monotonic() + self.period)

    def reset_immediately(self):
        """
        Reset the interval immediately. This is equivalent to calling reset_at(time.monotonic()).
        """
        self.reset_at(time.monotonic())

    def reset_at(self, instant):
        """
        Reset the interval to the specified instant. If the instant is in the past, then the behavior
        will be determined by the configured missed tick behavior. If the instant is in the future,
        then the next tick will complete at the given instant, even if that exceeds the period. If any
        ticks were missed, they will be skipped.
        """
        self.start = instant
        self.ticks = 0

    def reset_after(self, duration):
        """
        Reset the interval after the specified duration. This is equivalent to calling reset_at(time.monotonic() + duration).
        """
        self.reset_at(time.monotonic() + duration)

    def get_period(self):
        return self.period

    async def tick(self):
        self.ticks += 1
        now = time.monotonic()
        deadline = self.start + self.period * self.ticks
        if now > deadline + 1e-5:
            # If the deadline has passed, we need to determine how to handle the missed tick.
            match self.missed_tick_behavior:

                # If using the burst behavior, will return continue to return immediately until caught up.
                case MissedTickBehavior.Burst:
                    return

                # if using the delay behavior, we will return immediately once and future ticks will be offset by the delay.
                case MissedTickBehavior.Delay:
                    self.reset_at(now)
                    return

                # If using the skip behavior, we will skip the missed tick and return at the next period.
                case MissedTickBehavior.Skip:
                    self.ticks += (now - deadline) // self.period
                    return

        # If the deadline has not passed, wait until the deadline.
        #import pdb; pdb.set_trace()
        await asyncio.sleep(deadline - now)
