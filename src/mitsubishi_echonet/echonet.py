"""Minimal ECHONET Lite framing and probing.

ECHONET Lite devices exchange UDP datagrams on port 3610. A frame is:

    EHD1 EHD2 | TID (2) | SEOJ (3) | DEOJ (3) | ESV | OPC | properties...

Each property is EPC (1), PDC (1), then PDC bytes of EDT. This module builds
GET requests and parses replies - enough to ask an adapter "are you there?"
and to read a simple property. It is not a full ECHONET stack.

References:
- ECHONET Lite specification (free from the ECHONET Consortium)
- https://github.com/scottyphillips/echonetlite_homeassistant
"""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass

EHD1 = 0x10
EHD2 = 0x81
ECHONET_PORT = 3610
MULTICAST_GROUP = "224.0.23.0"

# ECHONET objects (EOJ) are 3 bytes: class group, class code, instance.
# As 24-bit ints: controller 0x05FF01, node profile 0x0EF001, home AC 0x013001.
EOJ_CONTROLLER = 0x05FF01
EOJ_NODE_PROFILE = 0x0EF001
EOJ_HOME_AIR_CONDITIONER = 0x013001

ESV_SET_I = 0x60
ESV_GET = 0x62
ESV_GET_RES = 0x72
ESV_INF = 0x73
ESV_GET_SNA = 0x52

EPC_OPERATION_STATUS = 0x80
EPC_SELF_NODE_INSTANCE_LIST_S = 0xD6

ESV_NAMES = {
    ESV_SET_I: "SetI",
    ESV_GET: "Get",
    ESV_GET_RES: "Get_Res",
    ESV_INF: "INF",
    ESV_GET_SNA: "Get_SNA",
}

CLASS_NAMES = {
    0x0130: "home air conditioner",
    0x0EF0: "node profile",
    0x05FF: "controller",
}

EPC_NAMES = {
    EPC_OPERATION_STATUS: "operation status",
    EPC_SELF_NODE_INSTANCE_LIST_S: "self-node instance list S",
    0xB3: "set temperature",
    0xA0: "operation mode",
    0xA3: "fan speed",
    0xBB: "measured room temperature",
}

_HEADER_LEN = 2 + 2 + 3 + 3 + 1 + 1  # EHD, TID, SEOJ, DEOJ, ESV, OPC


class FrameError(ValueError):
    """Raised when a datagram is not a well-formed ECHONET Lite frame."""


@dataclass(frozen=True)
class EchonetProperty:
    """One EPC/PDC/EDT triple."""

    epc: int
    edt: bytes = b""

    def __post_init__(self) -> None:
        if not 0x00 <= self.epc <= 0xFF:
            raise FrameError(f"EPC out of range: {self.epc:#x}")
        if len(self.edt) > 0xFF:
            raise FrameError(f"EDT too long: {len(self.edt)} bytes")


@dataclass(frozen=True)
class EchonetFrame:
    """A parsed or to-be-sent ECHONET Lite frame."""

    tid: int
    seoj: int
    deoj: int
    esv: int
    properties: tuple[EchonetProperty, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (("TID", self.tid),):
            if not 0 <= value <= 0xFFFF:
                raise FrameError(f"{name} out of range: {value:#x}")
        for name, value in (("SEOJ", self.seoj), ("DEOJ", self.deoj)):
            if not 0 <= value <= 0xFFFFFF:
                raise FrameError(f"{name} out of range: {value:#x}")
        if not 0 <= self.esv <= 0xFF:
            raise FrameError(f"ESV out of range: {self.esv:#x}")
        if len(self.properties) > 0xFF:
            raise FrameError(f"too many properties: {len(self.properties)}")

    def to_bytes(self) -> bytes:
        out = bytearray([EHD1, EHD2])
        out += self.tid.to_bytes(2, "big")
        out += self.seoj.to_bytes(3, "big")
        out += self.deoj.to_bytes(3, "big")
        out.append(self.esv)
        out.append(len(self.properties))
        for prop in self.properties:
            out.append(prop.epc)
            out.append(len(prop.edt))
            out += prop.edt
        return bytes(out)

    @classmethod
    def from_bytes(cls, data: bytes) -> EchonetFrame:
        if len(data) < _HEADER_LEN:
            raise FrameError(f"frame too short: {len(data)} bytes")
        if data[0] != EHD1 or data[1] != EHD2:
            raise FrameError(f"bad ECHONET header: {data[0]:#04x} {data[1]:#04x}")
        tid = int.from_bytes(data[2:4], "big")
        seoj = int.from_bytes(data[4:7], "big")
        deoj = int.from_bytes(data[7:10], "big")
        esv = data[10]
        opc = data[11]
        offset = _HEADER_LEN
        properties = []
        for _ in range(opc):
            if offset + 2 > len(data):
                raise FrameError("truncated property header")
            epc, pdc = data[offset], data[offset + 1]
            offset += 2
            if offset + pdc > len(data):
                raise FrameError(f"truncated property {epc:#04x}: want {pdc} bytes")
            properties.append(EchonetProperty(epc, data[offset : offset + pdc]))
            offset += pdc
        return cls(tid=tid, seoj=seoj, deoj=deoj, esv=esv, properties=tuple(properties))


def _class_code(eoj: int) -> int:
    """Class group + class code (top two bytes) of an EOJ."""
    return eoj >> 8


def describe(frame: EchonetFrame) -> str:
    """One-line human-readable summary of a frame."""
    src_class = CLASS_NAMES.get(_class_code(frame.seoj), f"class {frame.seoj:06x}")
    esv = ESV_NAMES.get(frame.esv, f"ESV {frame.esv:#04x}")
    parts = []
    for prop in frame.properties:
        name = EPC_NAMES.get(prop.epc, f"EPC {prop.epc:#04x}")
        value = prop.edt.hex() or "-"
        parts.append(f"{name}={value}")
    suffix = " " + ", ".join(parts) if parts else ""
    return f"{esv} from {src_class} ({frame.seoj:06x}){suffix}"


def build_get_request(
    tid: int,
    deoj: int = EOJ_HOME_AIR_CONDITIONER,
    epc: int = EPC_OPERATION_STATUS,
) -> bytes:
    """Build a GET request frame (controller asks one property of one object)."""
    return EchonetFrame(
        tid=tid,
        seoj=EOJ_CONTROLLER,
        deoj=deoj,
        esv=ESV_GET,
        properties=(EchonetProperty(epc),),
    ).to_bytes()


@dataclass(frozen=True)
class ProbeResult:
    """One responder to a probe."""

    address: str
    port: int
    frame: EchonetFrame


def _collect(sock: socket.socket, deadline: float) -> list[ProbeResult]:
    """Read datagrams until the deadline; keep the parseable ones."""
    results: list[ProbeResult] = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return results
        sock.settimeout(remaining)
        try:
            data, (addr, port) = sock.recvfrom(4096)
        except TimeoutError:
            return results
        try:
            frame = EchonetFrame.from_bytes(data)
        except FrameError:
            continue
        results.append(ProbeResult(address=addr, port=port, frame=frame))


def query(
    ip: str,
    *,
    tid: int = 1,
    deoj: int = EOJ_HOME_AIR_CONDITIONER,
    epc: int = EPC_OPERATION_STATUS,
    timeout: float = 3.0,
    sock: socket.socket | None = None,
) -> ProbeResult | None:
    """Send one GET to a specific adapter; return its first reply, if any.

    A reply means the adapter has ECHONET Lite enabled. Silence means it is
    disabled, unreachable, or filtered - pair with a TCP reachability check to
    tell those apart.
    """
    own_sock = sock is None
    if sock is None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(build_get_request(tid, deoj, epc), (ip, ECHONET_PORT))
        results = _collect(sock, time.monotonic() + timeout)
        return results[0] if results else None
    finally:
        if own_sock:
            sock.close()


def discover(
    *,
    tid: int = 1,
    timeout: float = 3.0,
    multicast_group: str = MULTICAST_GROUP,
    sock: socket.socket | None = None,
) -> list[ProbeResult]:
    """Multicast a node-profile instance-list GET; return every responder."""
    own_sock = sock is None
    if sock is None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        request = build_get_request(tid, EOJ_NODE_PROFILE, EPC_SELF_NODE_INSTANCE_LIST_S)
        sock.sendto(request, (multicast_group, ECHONET_PORT))
        return _collect(sock, time.monotonic() + timeout)
    finally:
        if own_sock:
            sock.close()


def tcp_reachable(ip: str, port: int = 80, timeout: float = 3.0) -> bool:
    """True when a TCP connection to the adapter's HTTP port succeeds."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False
