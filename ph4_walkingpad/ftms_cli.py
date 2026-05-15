"""
FTMS WalkingPad CLI

Simple command-line interface for the FTMS WalkingPad controller.
Supports start, stop, and monitor commands for newer Kingsmith models
(PH4, X21, etc.) that use the standard Bluetooth FTMS profile.
"""

import argparse
import asyncio
import logging
import sys

from ph4_walkingpad.ftms_pad import WalkingPadFTMS

logger = logging.getLogger(__name__)


async def cmd_start(args: argparse.Namespace) -> None:
    """Connect and start the walking pad."""
    async with WalkingPadFTMS(args.address) as pad:
        speed = args.speed
        print(f"Starting at {speed} km/h...")
        ok = await pad.start(speed=speed)
        print(f"Result: {'ok' if ok else 'failed'}, speed={pad.state.speed_kmh:.1f} km/h")


async def cmd_stop(args: argparse.Namespace) -> None:
    """Pause/stop the walking pad."""
    async with WalkingPadFTMS(args.address) as pad:
        ok = await pad.pause()
        print("Stopped." if ok else "Stop failed.")


async def cmd_monitor(args: argparse.Namespace) -> None:
    """Connect and display telemetry."""
    async with WalkingPadFTMS(args.address) as pad:

        def on_change(state):
            print(f"  {state}")

        pad.on_state_change(on_change)
        print("Monitoring... (Ctrl+C to stop)")
        try:
            await asyncio.sleep(300)
        except KeyboardInterrupt:
            pass


def main() -> None:
    """Entry point for the ftms-walkingpad-ctl CLI."""
    parser = argparse.ArgumentParser(
        prog="ftms-walkingpad-ctl",
        description="Control Kingsmith WalkingPad via FTMS Bluetooth LE",
    )
    parser.add_argument("--address", required=True, help="BLE device address")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # start
    start_parser = subparsers.add_parser("start", help="Start the walking pad")
    start_parser.add_argument("--speed", type=float, default=3.0, help="Target speed in km/h (default: 3.0)")

    # stop
    subparsers.add_parser("stop", help="Pause/stop the walking pad")

    # monitor
    subparsers.add_parser("monitor", help="Monitor walking pad telemetry")

    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    cmd_map = {
        "start": cmd_start,
        "stop": cmd_stop,
        "monitor": cmd_monitor,
    }

    try:
        asyncio.run(cmd_map[args.command](args))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
