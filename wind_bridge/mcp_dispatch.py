"""Run blocking tool bodies without blocking MCP message handling.

Wait for execution slots asynchronously, before allocating a worker thread. A
cancelled queued call therefore does not execute or reserve a Wind request.
Started calls finish before releasing their slot, preserving their receipts and
native serialization. The existing cross-process native lock remains in force.
"""
from __future__ import annotations

import functools
import inspect
import logging
import time

import anyio
from anyio.lowlevel import RunVar, checkpoint_if_cancelled

logger = logging.getLogger("wind_terminal_api.mcp_dispatch")
_LOCAL_WORKERS = 8
_STATE = RunVar("wind_terminal_api_dispatch")


class _DispatchState:
    def __init__(self):
        self.native_gate = anyio.Lock()
        self.local_slots = anyio.Semaphore(_LOCAL_WORKERS)
        # Neither native queues nor local work borrow the default limiter used
        # by the SDK's stdio reader/writer. Gates admit at most nine workers.
        self.worker_limiter = anyio.CapacityLimiter(_LOCAL_WORKERS + 1)


def _state():
    state = _STATE.get(None)
    if state is None:
        state = _DispatchState()
        _STATE.set(state)
    return state


def _invoke(fn, args, kwargs, native, queued_at):
    started = time.monotonic()
    name = getattr(fn, "__name__", type(fn).__name__)
    logger.info("tool=%s native=%s queue_seconds=%.3f", name, native, started - queued_at)
    try:
        return fn(*args, **kwargs)
    finally:
        logger.info("tool=%s native=%s execution_seconds=%.3f", name, native,
                    time.monotonic() - started)


async def _finish_worker(fn, args, kwargs, native, queued_at, limiter):
    outcome = []

    async def invoke():
        # The task group waits for this child even on direct asyncio Task.cancel(),
        # so the parent cannot release its gate before the body finishes.
        # run_sync shields started work; capturing errors avoids ExceptionGroup.
        try:
            value = await anyio.to_thread.run_sync(
                _invoke, fn, args, kwargs, native, queued_at, limiter=limiter)
        except BaseException as exc:
            outcome.append((False, exc))
        else:
            outcome.append((True, value))

    async with anyio.create_task_group() as group:
        group.start_soon(invoke)
    succeeded, value = outcome[0]
    if not succeeded:
        raise value
    return value


async def call_in_thread(fn, args=(), kwargs=None, *, native=True):
    """Cancel while queued; finish already-started work before releasing its slot."""
    state = _state()
    queued_at = time.monotonic()
    gate = state.native_gate if native else state.local_slots
    async with gate:
        await checkpoint_if_cancelled()
        return await _finish_worker(fn, args, kwargs or {}, bool(native), queued_at,
                                    state.worker_limiter)


def register_tool(app, annotations, *, native=True, native_if=None):
    """Register an async MCP wrapper while retaining the synchronous CLI binding."""
    def decorate(fn):
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            serialized = bool(native)
            if native_if is not None:
                bound = signature.bind(*args, **kwargs)
                bound.apply_defaults()
                try:
                    serialized = bool(native_if(bound.arguments))
                except Exception:
                    logger.exception("native predicate failed for %s; serializing", fn.__name__)
                    serialized = True
            return await call_in_thread(fn, args, kwargs, native=serialized)

        app.tool(annotations=annotations)(wrapper)
        return fn

    return decorate
