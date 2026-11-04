"""Command-line interface: enable, disable, and verify local ECHONET Lite."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import __version__, cli_spec, echonet, smart


def _add_opt(parser: argparse.ArgumentParser, opt: cli_spec.Opt) -> None:
    if opt.default is None:
        parser.add_argument(opt.flag, metavar=opt.metavar, help=opt.help, required=opt.required)
    else:
        parser.add_argument(
            opt.flag,
            metavar=opt.metavar,
            type=float,
            default=float(opt.default),
            help=opt.help,
            required=opt.required,
        )


def build_parser() -> argparse.ArgumentParser:
    # Names, help text and structure come from the canonical spec (#54).
    parser = argparse.ArgumentParser(
        prog=cli_spec.PROGRAM,
        description=cli_spec.PROGRAM_DESCRIPTION,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, command in cli_spec.COMMANDS.items():
        cmd_parser = sub.add_parser(name, help=command.summary)
        for opt in command.options:
            _add_opt(cmd_parser, opt)

    return parser


def _run_toggle(fn: object, ip: str, timeout: float, verb: str) -> int:
    assert callable(fn)
    result = fn(ip, timeout=timeout)
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


def _report_no_udp_reply(ip: str, timeout: float) -> None:
    """Report the reachable-HTTP / silent-UDP state without over-claiming (#70).

    UDP silence alone cannot establish the firmware setting: Wi-Fi client
    isolation, host firewalls and reply-port binding all drop replies. The
    adapter's own HTTP-reported ECHONET flag is shown as a separate fact.
    """
    print(f"{ip}: adapter reachable on port 80, but no ECHONET reply on UDP 3610.")
    flag = smart.query_echonet_flag(ip, timeout=timeout)
    if flag is None:
        print("Adapter HTTP status did not report an ECHONET flag.")
        print(
            "ECHONET Lite state unknown: no UDP reply alone cannot confirm it is off. "
            f"Cross-check with: scripts/ac_smart.py status --ip {ip}"
        )
    elif flag.upper() == "OFF":
        print("Adapter HTTP status reports ECHONET: OFF.")
        print(f"ECHONET Lite is off. Run 'mitsubishi-echonet enable --ip {ip}' to switch it on.")
    elif flag.upper() == "ON":
        print(f"Adapter HTTP status reports ECHONET: {flag}.")
        print(
            "ECHONET Lite state unknown: the adapter reports it on, so the missing "
            "replies point at the network (Wi-Fi client isolation or a host firewall), "
            "not the adapter. See the README troubleshooting section."
        )
    else:
        print(f"Adapter HTTP status reports an unrecognized ECHONET flag: {flag!r}.")
        print(
            "ECHONET Lite state unknown: no UDP reply alone cannot confirm it is off. "
            f"Cross-check with: scripts/ac_smart.py status --ip {ip}"
        )


def _cmd_status(ip: str, timeout: float) -> int:
    reachable = echonet.tcp_reachable(ip, 80, timeout=timeout)
    reply = echonet.query(ip, timeout=timeout)
    if reply is not None:
        print(f"{ip}: ECHONET Lite is ENABLED - {echonet.describe(reply.frame)}")
        return 0
    if reachable:
        _report_no_udp_reply(ip, timeout)
        return 1
    print(
        f"{ip}: no ECHONET reply and port 80 unreachable - adapter offline or wrong IP/network.",
        file=sys.stderr,
    )
    return 1


def _cmd_capabilities(ip: str, timeout: float) -> int:
    maps = echonet.read_property_maps(ip, timeout=timeout)
    if all(value is None for value in maps.values()):
        if echonet.tcp_reachable(ip, 80, timeout=timeout):
            _report_no_udp_reply(ip, timeout)
        else:
            print(
                f"{ip}: no ECHONET reply and port 80 unreachable",
                file=sys.stderr,
            )
            print("Adapter offline or wrong IP/network.", file=sys.stderr)
        return 1
    labels = (
        (echonet.EPC_STATUS_CHANGE_MAP, "status-change"),
        (echonet.EPC_SET_PROPERTY_MAP, "set"),
        (echonet.EPC_GET_PROPERTY_MAP, "get"),
    )
    for map_epc, label in labels:
        props = maps[map_epc]
        if props is None:
            print(f"{label} property map: no reply")
            continue
        print(f"{label} property map ({len(props)} properties):")
        for code in sorted(props):
            print(f"  {code:#04x} {echonet.EPC_NAMES.get(code, '-')}")
    get_map = maps[echonet.EPC_GET_PROPERTY_MAP] or []
    set_map = maps[echonet.EPC_SET_PROPERTY_MAP] or []
    timers = sorted(code for code in echonet.TIMER_EPCS if code in get_map or code in set_map)
    if timers:
        print(
            "Timer properties exposed: "
            + ", ".join(f"{code:#04x} {echonet.EPC_NAMES[code]}" for code in timers)
        )
    else:
        print("No ECHONET timer properties (0x90-0x96) exposed by this adapter.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "enable":
        return _run_toggle(smart.enable_echonet, args.ip, args.timeout, "enabled")
    if args.command == "disable":
        return _run_toggle(smart.disable_echonet, args.ip, args.timeout, "disabled")
    if args.command == "status":
        return _cmd_status(args.ip, args.timeout)
    if args.command == "capabilities":
        return _cmd_capabilities(args.ip, args.timeout)
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
