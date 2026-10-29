"""Command-line interface: enable, disable, and verify local ECHONET Lite."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import __version__, echonet, smart


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ip", required=True, help="adapter IP address on the local network")
    parser.add_argument("--timeout", type=float, default=3.0, help="seconds to wait (default 3)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mitsubishi-echonet",
        description=(
            "Enable, disable and verify local ECHONET Lite control on Mitsubishi "
            "Electric MAC-577IF-E-class Wi-Fi adapters. Run only on a trusted "
            "network: ECHONET Lite has no authentication."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    enable = sub.add_parser("enable", help="enable ECHONET Lite via the /smart endpoint")
    _add_common(enable)

    disable = sub.add_parser("disable", help="disable ECHONET Lite via the /smart endpoint")
    _add_common(disable)

    status = sub.add_parser(
        "status",
        help="read-only check: is the adapter reachable and is ECHONET Lite on?",
    )
    _add_common(status)

    disc = sub.add_parser("discover", help="multicast discovery of ECHONET Lite devices")
    disc.add_argument("--timeout", type=float, default=3.0, help="seconds to listen (default 3)")

    return parser


def _run_toggle(fn: object, ip: str, timeout: float, verb: str) -> int:
    assert callable(fn)
    result = fn(ip, timeout=max(timeout, 10.0))
    if result.ok:
        print(f"ECHONET Lite {verb}: adapter at {ip} accepted the command (HTTP {result.status}).")
        print("Verify with: mitsubishi-echonet status --ip " + ip)
        return 0
    print(f"Failed to {verb} ECHONET Lite at {ip}: {result.error}", file=sys.stderr)
    if result.body:
        print(f"Response body: {result.body[:200]}", file=sys.stderr)
    print(
        "Check that the IP is right and this machine is on the same network. "
        "If the adapter's key is not the factory default this command cannot work; "
        "see docs/DESIGN.md.",
        file=sys.stderr,
    )
    return 1


def _cmd_status(ip: str, timeout: float) -> int:
    reachable = echonet.tcp_reachable(ip, 80, timeout=timeout)
    reply = echonet.query(ip, timeout=timeout)
    if reply is not None:
        print(f"{ip}: ECHONET Lite is ENABLED - {echonet.describe(reply.frame)}")
        return 0
    if reachable:
        print(f"{ip}: adapter reachable on port 80, but no ECHONET reply - ECHONET Lite is OFF.")
        return 1
    print(
        f"{ip}: no ECHONET reply and port 80 unreachable - adapter offline or wrong IP/network.",
        file=sys.stderr,
    )
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "enable":
        return _run_toggle(smart.enable_echonet, args.ip, args.timeout, "enabled")
    if args.command == "disable":
        return _run_toggle(smart.disable_echonet, args.ip, args.timeout, "disabled")
    if args.command == "status":
        return _cmd_status(args.ip, args.timeout)
    if args.command == "discover":
        results = echonet.discover(timeout=args.timeout)
        if not results:
            print(f"No ECHONET Lite devices answered within {args.timeout}s.")
            return 1
        for result in results:
            print(f"{result.address}: {echonet.describe(result.frame)}")
        return 0
    raise AssertionError(f"unhandled command {args.command}")


def run() -> None:
    """Console-script entry point."""
    sys.exit(main())
