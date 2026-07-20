"""In-memory concurrency limiter: per-instance and global caps."""

from __future__ import annotations

import asyncio

import pytest

from app.services.concurrency import InMemoryConcurrencyLimiter

pytestmark = pytest.mark.asyncio


async def test_per_instance_limit_blocks_third() -> None:
    limiter = InMemoryConcurrencyLimiter(per_instance=2, global_limit=10)
    held = asyncio.Event()
    release = asyncio.Event()
    running = 0
    peak = 0

    async def worker() -> None:
        nonlocal running, peak
        async with limiter.slot("inst-a"):
            running += 1
            peak = max(peak, running)
            held.set()
            await release.wait()
            running -= 1

    tasks = [asyncio.create_task(worker()) for _ in range(3)]
    await asyncio.sleep(0.02)
    # Only 2 may run concurrently on the same instance.
    assert peak <= 2
    release.set()
    await asyncio.gather(*tasks)


async def test_global_limit_caps_across_instances() -> None:
    limiter = InMemoryConcurrencyLimiter(per_instance=5, global_limit=2)
    running = 0
    peak = 0
    release = asyncio.Event()

    async def worker(inst: str) -> None:
        nonlocal running, peak
        async with limiter.slot(inst):
            running += 1
            peak = max(peak, running)
            await release.wait()
            running -= 1

    tasks = [asyncio.create_task(worker(f"inst-{i}")) for i in range(4)]
    await asyncio.sleep(0.02)
    assert peak <= 2  # global cap dominates even across distinct instances
    release.set()
    await asyncio.gather(*tasks)


async def test_slot_released_on_exception() -> None:
    limiter = InMemoryConcurrencyLimiter(per_instance=1, global_limit=1)

    with pytest.raises(RuntimeError):
        async with limiter.slot("inst"):
            raise RuntimeError("boom")

    # The slot must be free again despite the error.
    async with limiter.slot("inst"):
        pass
