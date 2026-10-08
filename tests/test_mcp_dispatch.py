"""Offline dispatch regressions: fake work only, without a server or WindPy."""

import asyncio
import inspect
import threading
import unittest

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.tools.base import Tool
from mcp.types import ToolAnnotations

from wind_bridge.mcp_dispatch import call_in_thread, register_tool


async def wait_for(predicate):
    """Wait for observable state, with a generous deadlock guard."""
    with anyio.fail_after(3):
        while not predicate():
            await anyio.sleep(0.001)


def wait_in_fake_worker(event):
    # A broken dispatcher must fail the test instead of leaving a worker alive.
    if not event.wait(3):
        raise TimeoutError("test did not release fake worker")


class McpDispatchTests(unittest.TestCase):
    def test_native_calls_are_serial_while_local_calls_and_loop_progress(self):
        async def scenario():
            release = threading.Event()
            guard = threading.Lock()
            state = {"active": 0, "peak": 0, "started": [], "results": []}

            def native(value):
                with guard:
                    state["active"] += 1
                    state["peak"] = max(state["peak"], state["active"])
                    state["started"].append(value)
                try:
                    wait_in_fake_worker(release)
                    return value
                finally:
                    with guard:
                        state["active"] -= 1

            async def run_native(value):
                state["results"].append(await call_in_thread(native, (value,)))

            async with anyio.create_task_group() as group:
                try:
                    for value in range(6):
                        group.start_soon(run_native, value)
                    await wait_for(lambda: state["active"] == 1)
                    ticks = 0
                    for _ in range(5):
                        await anyio.sleep(0)
                        ticks += 1
                    with anyio.fail_after(3):
                        local_thread = await call_in_thread(threading.get_ident, native=False)
                    self.assertNotEqual(local_thread, threading.get_ident())
                    self.assertEqual(ticks, 5)
                    self.assertEqual(len(state["started"]), 1)
                finally:
                    release.set()
            self.assertEqual(state["peak"], 1)
            self.assertEqual(sorted(state["results"]), list(range(6)))

        anyio.run(scenario)

    def test_anyio_cancelled_waiter_never_starts_body(self):
        async def scenario():
            release = threading.Event()
            started = threading.Event()
            entered = anyio.Event()
            finished = anyio.Event()
            scopes = []
            receipts = []

            def leader():
                started.set()
                wait_in_fake_worker(release)

            async def queued():
                try:
                    with anyio.CancelScope() as scope:
                        scopes.append(scope)
                        entered.set()
                        await call_in_thread(lambda: receipts.append("unexpected receipt"))
                finally:
                    finished.set()

            async with anyio.create_task_group() as group:
                try:
                    group.start_soon(call_in_thread, leader)
                    await wait_for(started.is_set)
                    group.start_soon(queued)
                    await entered.wait()
                    scopes[0].cancel()
                    with anyio.fail_after(3):
                        await finished.wait()
                    self.assertEqual(receipts, [])
                finally:
                    release.set()
            self.assertEqual(receipts, [])

        anyio.run(scenario)

    def test_asyncio_cancelled_waiter_never_starts_body(self):
        async def scenario():
            release = threading.Event()
            started = threading.Event()
            receipts = []

            def leader():
                started.set()
                wait_in_fake_worker(release)

            async with anyio.create_task_group() as group:
                waiter = None
                try:
                    group.start_soon(call_in_thread, leader)
                    await wait_for(started.is_set)
                    waiter = asyncio.create_task(
                        call_in_thread(lambda: receipts.append("unexpected receipt")))
                    await anyio.sleep(0)
                    waiter.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await waiter
                finally:
                    release.set()
                    if waiter is not None and not waiter.done():
                        waiter.cancel()
                        await asyncio.gather(waiter, return_exceptions=True)
            self.assertEqual(receipts, [])

        anyio.run(scenario)

    def test_anyio_active_cancellation_preserves_receipt_and_serialization(self):
        async def scenario():
            release = threading.Event()
            active_started = threading.Event()
            next_started = threading.Event()
            active_finished = anyio.Event()
            scopes = []
            events = []

            def active():
                events.append("active start")
                active_started.set()
                wait_in_fake_worker(release)
                events.append("active receipt")

            def following():
                next_started.set()
                events.append("following receipt")

            async def run_active():
                try:
                    with anyio.CancelScope() as scope:
                        scopes.append(scope)
                        await call_in_thread(active)
                finally:
                    active_finished.set()

            async with anyio.create_task_group() as group:
                try:
                    group.start_soon(run_active)
                    await wait_for(active_started.is_set)
                    scopes[0].cancel()
                    group.start_soon(call_in_thread, following)
                    for _ in range(5):
                        await anyio.sleep(0)
                    self.assertFalse(active_finished.is_set())
                    self.assertFalse(next_started.is_set())
                finally:
                    release.set()
            self.assertEqual(events, ["active start", "active receipt", "following receipt"])

        anyio.run(scenario)

    def test_asyncio_active_cancellation_cannot_release_native_slot_early(self):
        async def scenario():
            release = threading.Event()
            active_started = threading.Event()
            next_started = threading.Event()
            events = []

            def active():
                events.append("active start")
                active_started.set()
                wait_in_fake_worker(release)
                events.append("active receipt")

            def following():
                next_started.set()
                events.append("following receipt")

            tasks = []
            try:
                tasks.append(asyncio.create_task(call_in_thread(active)))
                await wait_for(active_started.is_set)
                tasks[0].cancel()
                tasks.append(asyncio.create_task(call_in_thread(following)))
                for _ in range(5):
                    await anyio.sleep(0)
                self.assertFalse(next_started.is_set())
                tasks[0].cancel()
                for _ in range(5):
                    await anyio.sleep(0)
                self.assertFalse(next_started.is_set())
            finally:
                release.set()
                with anyio.fail_after(3):
                    await asyncio.gather(*tasks, return_exceptions=True)
            self.assertEqual(events, ["active start", "active receipt", "following receipt"])

        anyio.run(scenario)

    def test_queued_native_and_busy_local_work_leave_default_thread_pool_free(self):
        async def scenario():
            native_release = threading.Event()
            local_release = threading.Event()
            guard = threading.Lock()
            started = {"native": 0, "local": 0}

            def native():
                with guard:
                    started["native"] += 1
                wait_in_fake_worker(native_release)

            def local():
                with guard:
                    started["local"] += 1
                wait_in_fake_worker(local_release)

            async def run_local():
                await call_in_thread(local, native=False)

            async with anyio.create_task_group() as group:
                try:
                    for _ in range(50):
                        group.start_soon(call_in_thread, native)
                    for _ in range(48):
                        group.start_soon(run_local)
                    await wait_for(lambda: started["native"] == 1 and started["local"] == 8)
                    default_pool = anyio.to_thread.current_default_thread_limiter()
                    self.assertEqual(default_pool.borrowed_tokens, 0)
                    with anyio.fail_after(3):
                        result = await anyio.to_thread.run_sync(lambda: "protocol worker available")
                    self.assertEqual(result, "protocol worker available")
                    self.assertEqual(started, {"native": 1, "local": 8})
                finally:
                    native_release.set()
                    local_release.set()
            self.assertEqual(started, {"native": 50, "local": 48})

        anyio.run(scenario)

    def test_cancelled_local_waiter_never_starts_after_busy_slots_release(self):
        async def scenario():
            release = threading.Event()
            guard = threading.Lock()
            entered = anyio.Event()
            finished = anyio.Event()
            scopes = []
            started = []
            receipts = []
            cancelled_receipts = []

            def local(value):
                with guard:
                    started.append(value)
                wait_in_fake_worker(release)
                with guard:
                    receipts.append(value)

            async def run_local(value):
                await call_in_thread(local, (value,), native=False)

            async def queued():
                try:
                    with anyio.CancelScope() as scope:
                        scopes.append(scope)
                        entered.set()
                        await call_in_thread(
                            lambda: cancelled_receipts.append("unexpected receipt"),
                            native=False)
                finally:
                    finished.set()

            async with anyio.create_task_group() as group:
                try:
                    for value in range(8):
                        group.start_soon(run_local, value)
                    await wait_for(lambda: len(started) == 8)
                    group.start_soon(queued)
                    await entered.wait()
                    scopes[0].cancel()
                    with anyio.fail_after(3):
                        await finished.wait()
                    self.assertEqual(cancelled_receipts, [])
                    self.assertEqual(receipts, [])
                finally:
                    release.set()
            self.assertEqual(sorted(receipts), list(range(8)))
            self.assertEqual(cancelled_receipts, [])

        anyio.run(scenario)

    def test_exception_releases_native_slot(self):
        async def scenario():
            def broken():
                raise ValueError("fake native failure")

            with self.assertRaisesRegex(ValueError, "fake native failure"):
                await call_in_thread(broken)
            with anyio.fail_after(3):
                self.assertEqual(await call_in_thread(lambda: "next receipt"), "next receipt")

        anyio.run(scenario)

    def test_registration_preserves_tool_contract_and_sync_cli_binding(self):
        app = FastMCP("offline-dispatch-test")
        annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False)

        def echo(value: int, labels: list[str] | None = None) -> dict:
            """Return a fake receipt with optional labels."""
            return {"value": value, "labels": labels, "thread": threading.get_ident()}

        expected = Tool.from_function(echo, annotations=annotations)
        original = register_tool(app, annotations, native=False)(echo)
        self.assertIs(original, echo)
        self.assertFalse(inspect.iscoroutinefunction(original))
        self.assertEqual(original(7)["value"], 7)
        registered = app._tool_manager.get_tool("echo")
        self.assertTrue(inspect.iscoroutinefunction(registered.fn))

        async def scenario():
            tools = await app.list_tools()
            self.assertEqual(len(tools), 1)
            actual = tools[0]
            self.assertEqual(actual.name, expected.name)
            self.assertEqual(actual.description, expected.description)
            self.assertEqual(actual.inputSchema, expected.parameters)
            self.assertEqual(actual.outputSchema, expected.output_schema)
            self.assertEqual(actual.annotations, annotations)
            result = await app._tool_manager.call_tool("echo", {"value": 8})
            self.assertEqual(result["value"], 8)
            self.assertIsNone(result["labels"])
            self.assertNotEqual(result["thread"], threading.get_ident())

        anyio.run(scenario)

    def test_conditional_native_routing_uses_validated_default_arguments(self):
        app = FastMCP("offline-conditional-dispatch-test")

        @register_tool(app, ToolAnnotations(readOnlyHint=True),
                       native_if=lambda kwargs: kwargs["scope"] != "local")
        def search(scope: str = "local") -> dict:
            """Fake local search, without data access."""
            return {"scope": scope}

        async def scenario():
            release = threading.Event()
            started = threading.Event()

            def leader():
                started.set()
                wait_in_fake_worker(release)

            async with anyio.create_task_group() as group:
                try:
                    group.start_soon(call_in_thread, leader)
                    await wait_for(started.is_set)
                    with anyio.fail_after(3):
                        result = await app._tool_manager.call_tool("search", {})
                    self.assertEqual(result, {"scope": "local"})
                finally:
                    release.set()

        anyio.run(scenario)

    def test_contended_native_gate_is_reusable_across_event_loop_lifetimes(self):
        async def scenario():
            release = threading.Event()
            started = threading.Event()
            values = []

            def leader():
                started.set()
                wait_in_fake_worker(release)
                return "leader"

            async def run(fn):
                values.append(await call_in_thread(fn))

            async with anyio.create_task_group() as group:
                try:
                    group.start_soon(run, leader)
                    await wait_for(started.is_set)
                    group.start_soon(run, lambda: "follower")
                    for _ in range(3):
                        await anyio.sleep(0)
                finally:
                    release.set()
            self.assertEqual(values, ["leader", "follower"])

        for _ in range(3):
            anyio.run(scenario)


if __name__ == "__main__":
    unittest.main()
