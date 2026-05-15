"""
FTMS WalkingPad BLE Controller

Controller for newer Kingsmith WalkingPad models (PH4, X21, etc.) that use
the standard Bluetooth FTMS (Fitness Machine Service) profile instead of
the proprietary Kingsmith binary protocol on chars 0xFE01/0xFE02.

The command queue handles all timing quirks internally:
- Auto-requests control before any command
- Auto-waits for RUNNING state before Set Speed (2.5s startup delay)
- Auto-retries on "Control Not Permitted" with exponential backoff
- Speed command cancellation (rapid set_speed calls cancel stale ones)
- Pause cancels pending speed changes
- No sleep() calls needed in user code
"""

import asyncio
import logging
import struct
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable

from bleak import BleakClient

logger = logging.getLogger(__name__)

# BLE UUIDs
FTMS_SERVICE = "00001826-0000-1000-8000-00805f9b34fb"
CONTROL_POINT = "00002ad9-0000-1000-8000-00805f9b34fb"
TREADMILL_DATA = "00002acd-0000-1000-8000-00805f9b34fb"

# Control Point opcodes
OP_REQUEST_CONTROL = 0x00
OP_RESET = 0x01
OP_SET_SPEED = 0x02
OP_START_RESUME = 0x07
OP_STOP_PAUSE = 0x08

# Indication result codes
RESULT_SUCCESS = 1
RESULT_NOT_SUPPORTED = 2
RESULT_INVALID_PARAM = 3
RESULT_OPERATION_FAILED = 4
RESULT_CONTROL_NOT_PERMITTED = 5

RESULT_NAMES = {
    1: "Success",
    2: "Not Supported",
    3: "Invalid Parameter",
    4: "Operation Failed",
    5: "Control Not Permitted",
}


class BeltState(Enum):
    """Logical treadmill belt state."""

    IDLE = auto()
    STARTING = auto()
    RUNNING = auto()
    PAUSING = auto()


@dataclass
class TreadmillState:
    """Current treadmill telemetry."""

    speed_kmh: float = 0.0
    elapsed_seconds: int = 0
    raw_speed: int = 0
    belt_state: BeltState = field(default_factory=lambda: BeltState.IDLE)

    def __repr__(self) -> str:
        return f"TreadmillState(belt={self.belt_state.name}, speed={self.speed_kmh:.1f}km/h, elapsed={self.elapsed_seconds}s)"


class _Command:
    """Internal command queued for execution."""

    __slots__ = ("op", "data", "future", "kind", "speed_kmh")

    def __init__(
        self,
        op: str,
        data: bytes,
        future: asyncio.Future,
        kind: str = "cmd",
        speed_kmh: float | None = None,
    ):
        self.op = op
        self.data = data
        self.future = future
        self.kind = kind  # "cmd" | "speed" — speed commands are cancellable
        self.speed_kmh = speed_kmh


class WalkingPadFTMS:
    """FTMS WalkingPad BLE controller with automatic command queue.

    The queue handles all timing quirks internally:
    - Auto-requests control before any command
    - Waits for the belt to enter RUNNING state before sending Set Speed
    - Retries Set Speed on "Control Not Permitted" with backoff
    - Speed commands cancel previous in-flight speed commands (no stale ramps)
    - Pause cancels pending speed changes

    Users never need sleep() calls — just call what you want.
    """

    # How long to wait after Start before the belt accepts Set Speed
    STARTUP_DELAY = 2.5
    # Retry backoff for "Control Not Permitted" on Set Speed
    RETRY_DELAYS = [0.5, 1.0, 1.5, 2.0]
    # Max time to wait for RUNNING state before giving up on a speed command
    RUNNING_STATE_TIMEOUT = 8.0

    def __init__(self, address: str) -> None:
        self.address = address
        self.client: BleakClient | None = None
        self.state = TreadmillState()

        # Command queue
        self._queue: asyncio.Queue[_Command] = asyncio.Queue()
        self._processor_task: asyncio.Task | None = None

        # Control point indication tracking
        self._ctrl_responses: dict[int, asyncio.Future] = {}

        # State machine internals
        self._has_control = False
        self._running_event = asyncio.Event()
        self._pending_speed: _Command | None = None

        # Callbacks
        self._on_state_change: list[Callable[[TreadmillState], None]] = []

    # ── Public API ──────────────────────────────────────────────────────

    def on_state_change(self, callback: Callable[[TreadmillState], None]) -> None:
        """Register callback for state updates."""
        self._on_state_change.append(callback)

    async def connect(self) -> None:
        """Connect to the treadmill and start the command processor."""
        self.client = BleakClient(self.address)
        await self.client.connect()

        await self.client.start_notify(TREADMILL_DATA, self._on_treadmill_data)
        await self.client.start_notify(CONTROL_POINT, self._on_control_indicate)

        self._processor_task = asyncio.create_task(self._process_queue())
        logger.info("Connected to WalkingPad FTMS")

    async def disconnect(self) -> None:
        """Disconnect from the treadmill."""
        # Cancel any pending commands
        while not self._queue.empty():
            try:
                cmd = self._queue.get_nowait()
                if not cmd.future.done():
                    cmd.future.cancel()
            except asyncio.QueueEmpty:
                break
        if self._pending_speed and not self._pending_speed.future.done():
            self._pending_speed.future.cancel()

        if self._processor_task:
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass

        if self.client and self.client.is_connected:
            await self.client.disconnect()
            logger.info("Disconnected from WalkingPad FTMS")

    async def __aenter__(self) -> "WalkingPadFTMS":
        await self.connect()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.disconnect()

    async def start(self, speed: float | None = None) -> bool:
        """Start the belt. Optionally set a target speed immediately.

        This handles the startup timing internally — no sleep needed.
        If speed is provided, it will be sent once the belt is ready.
        """
        cmd = _Command("start", b"\x07", self._loop().create_future())
        await self._queue.put(cmd)
        result = await cmd.future

        if result and speed is not None:
            return await self.set_speed(speed)
        return result

    async def set_speed(self, kmh: float) -> bool:
        """Set the belt speed in km/h.

        If the belt isn't running yet, this queues the speed change
        to be sent once it is. If a previous speed change is still
        in-flight, it's cancelled and replaced with this one.
        """
        speed_raw = int(kmh * 100)
        speed_bytes = struct.pack("<H", speed_raw)
        cmd = _Command(
            "set_speed",
            bytes([0x02]) + speed_bytes,
            self._loop().create_future(),
            kind="speed",
            speed_kmh=kmh,
        )

        # Cancel any previous pending speed command
        if self._pending_speed and not self._pending_speed.future.done():
            logger.debug(f"Cancelling stale speed change to {self._pending_speed.speed_kmh:.1f} km/h")
            self._pending_speed.future.cancel()
        self._pending_speed = cmd

        await self._queue.put(cmd)
        return await cmd.future

    async def pause(self) -> bool:
        """Pause/stop the belt smoothly.

        Cancels any pending speed changes — they're irrelevant now.
        """
        # Cancel pending speed — belt is stopping
        if self._pending_speed and not self._pending_speed.future.done():
            self._pending_speed.future.cancel()
            self._pending_speed = None

        cmd = _Command("pause", bytes([0x08, 0x02]), self._loop().create_future())
        await self._queue.put(cmd)
        return await cmd.future

    async def stop(self) -> bool:
        """Stop/pause the belt (same as pause on this treadmill)."""
        return await self.pause()

    # ── Queue Processor (state machine) ────────────────────────────────

    async def _process_queue(self) -> None:
        """Background task: process commands in order with state awareness."""
        while True:
            try:
                cmd = await self._queue.get()
                await self._execute_command(cmd)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"Queue processor error: {e}")

    async def _execute_command(self, cmd: _Command) -> None:
        """Execute a single command with full state machine logic."""
        # Skip cancelled commands (e.g. stale speed changes)
        if cmd.future.done():
            return

        try:
            if cmd.op == "start":
                await self._exec_start(cmd)
            elif cmd.op == "set_speed":
                await self._exec_set_speed(cmd)
            elif cmd.op == "pause":
                await self._exec_pause(cmd)
            else:
                logger.warning(f"Unknown command: {cmd.op}")
                cmd.future.set_result(False)
        except Exception as e:
            logger.error(f"Error executing {cmd.op}: {e}")
            if not cmd.future.done():
                cmd.future.set_result(False)

    async def _ensure_control(self) -> bool:
        """Make sure we have control, requesting it if needed."""
        if self._has_control:
            return True
        result = await self._write_ctrl(b"\x00", wait_for=0x00)
        if result == RESULT_SUCCESS:
            self._has_control = True
            return True
        return False

    async def _exec_start(self, cmd: _Command) -> None:
        """Handle start command — includes the startup delay internally."""
        if not await self._ensure_control():
            cmd.future.set_result(False)
            return

        # If already running, no-op
        if self.state.belt_state == BeltState.RUNNING:
            cmd.future.set_result(True)
            return

        self.state.belt_state = BeltState.STARTING
        self._notify_state()

        result = await self._write_ctrl(b"\x07", wait_for=0x07)
        if result != RESULT_SUCCESS:
            self.state.belt_state = BeltState.IDLE
            self._notify_state()
            cmd.future.set_result(False)
            return

        # Wait for belt to spin up — this is the critical timing gap
        # The treadmill needs ~2.5s before it accepts Set Speed commands
        logger.debug(f"Waiting {self.STARTUP_DELAY}s for belt startup...")
        await asyncio.sleep(self.STARTUP_DELAY)

        # Transition to RUNNING — now Set Speed commands will work
        self.state.belt_state = BeltState.RUNNING
        self._running_event.set()
        self._notify_state()

        cmd.future.set_result(True)

    async def _exec_set_speed(self, cmd: _Command) -> None:
        """Handle set_speed with auto-retry on Control Not Permitted."""
        # If belt isn't running yet, wait for it
        if self.state.belt_state not in (BeltState.RUNNING, BeltState.STARTING):
            # Wait for running state with timeout
            self._running_event.clear()
            try:
                await asyncio.wait_for(self._running_event.wait(), timeout=self.RUNNING_STATE_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("Timed out waiting for RUNNING state to set speed")
                cmd.future.set_result(False)
                return

        if not await self._ensure_control():
            cmd.future.set_result(False)
            return

        # Try sending with retry on "Control Not Permitted"
        for attempt, delay in enumerate(self.RETRY_DELAYS):
            if cmd.future.done():
                return  # Cancelled while waiting

            result = await self._write_ctrl(cmd.data, wait_for=0x02)

            if result == RESULT_SUCCESS:
                logger.info(f"Speed set to {cmd.speed_kmh:.1f} km/h (attempt {attempt + 1})")
                cmd.future.set_result(True)
                return
            elif result == RESULT_CONTROL_NOT_PERMITTED:
                logger.debug(f"Control Not Permitted on attempt {attempt + 1}, retrying in {delay}s...")
                await asyncio.sleep(delay)
                continue
            else:
                logger.warning(f"Set Speed failed: {RESULT_NAMES.get(result, str(result))}")
                cmd.future.set_result(False)
                return

        # All retries exhausted
        logger.warning(f"Set Speed failed after {len(self.RETRY_DELAYS)} retries")
        cmd.future.set_result(False)

    async def _exec_pause(self, cmd: _Command) -> None:
        """Handle pause — cancels pending speed, transitions state."""
        if not await self._ensure_control():
            cmd.future.set_result(False)
            return

        result = await self._write_ctrl(bytes([0x08, 0x02]), wait_for=0x08)
        self._has_control = False  # Control is revoked after pause
        self._running_event.clear()
        self.state.belt_state = BeltState.PAUSING
        self._notify_state()

        # Wait for belt to actually stop (speed reaches 0)
        timeout = 8.0
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.state.speed_kmh < 0.1:
                break
            await asyncio.sleep(0.3)

        self.state.belt_state = BeltState.IDLE
        self._notify_state()
        cmd.future.set_result(result == RESULT_SUCCESS)

    # ── BLE I/O ────────────────────────────────────────────────────────

    def _on_treadmill_data(self, sender: object, data: bytearray) -> None:
        """Parse FTMS Treadmill Data notification.

        NOTE: Kingsmith WalkingPad PH4 puts speed at bytes[2:4] regardless
        of the FTMS flags field (flags say speed not present, but it is).
        This is a non-standard FTMS implementation — we parse empirically.
        """
        d = bytes(data)
        if len(d) < 4:
            return

        # Speed is always at bytes 2-3 (uint16 LE, 0.01 km/h units)
        if len(d) >= 4:
            self.state.raw_speed = struct.unpack_from("<H", d, 2)[0]
            self.state.speed_kmh = self.state.raw_speed / 100.0

        # Elapsed time at byte 12
        if len(d) >= 13:
            self.state.elapsed_seconds = d[12]

        self._notify_state()

    def _on_control_indicate(self, sender: object, data: bytearray) -> None:
        """Parse FTMS Control Point indication."""
        d = bytes(data)
        if len(d) >= 3 and d[0] == 0x80:
            opcode = d[1]
            result = d[2]
            result_name = RESULT_NAMES.get(result, f"Unknown({result})")
            if result != RESULT_SUCCESS:
                logger.warning(f"Control point: op=0x{opcode:02X} result={result_name}")
            else:
                logger.debug(f"Control point: op=0x{opcode:02X} result={result_name}")

            # Signal any waiting coroutines
            future = self._ctrl_responses.get(opcode)
            if future and not future.done():
                future.set_result(result)

            # Track control loss
            if opcode == OP_STOP_PAUSE and result == RESULT_SUCCESS:
                self._has_control = False

    async def _write_ctrl(self, data: bytes, wait_for: int | None = None, timeout: float = 3.0) -> int:
        """Write to control point and optionally wait for indication."""
        event: asyncio.Future[int] | None = None
        if wait_for is not None:
            event = self._loop().create_future()
            self._ctrl_responses[wait_for] = event

        assert self.client is not None
        await self.client.write_gatt_char(CONTROL_POINT, data, response=True)

        if wait_for is not None and event is not None:
            try:
                result = await asyncio.wait_for(event, timeout=timeout)
                return result
            except asyncio.TimeoutError:
                logger.warning(f"Timeout waiting for indication on op=0x{wait_for:02X}")
                return -1
            finally:
                self._ctrl_responses.pop(wait_for, None)
        return 0

    def _notify_state(self) -> None:
        """Fire state change callbacks."""
        for cb in self._on_state_change:
            try:
                cb(self.state)
            except Exception:
                pass

    def _loop(self) -> asyncio.AbstractEventLoop:
        """Get the current event loop."""
        return asyncio.get_running_loop()
