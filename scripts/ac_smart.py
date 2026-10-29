#!/usr/bin/env python3
"""ac_smart.py - local control of a Mitsubishi MAC-577IF-E(-class) Wi-Fi adapter.

Talks to the adapter's unauthenticated local /smart HTTP endpoint (the same
channel the MELCloud Home app uses on the LAN). No cloud, no dependencies:
Python 3.10+ standard library only.

Usage (global options work before or after the subcommand):
    python3 ac_smart.py --ip 192.168.88.30 status        # read-only: power, mode, temps, energy
    python3 ac_smart.py --ip 192.168.88.30 on            # power on (keeps last mode/temp)
    python3 ac_smart.py --ip 192.168.88.30 off           # power off
    python3 ac_smart.py --ip 192.168.88.30 set --mode cool --temp 24 --fan auto
    python3 ac_smart.py --ip 192.168.88.30 set --vane-v swing --vane-h center
    python3 ac_smart.py --ip 192.168.88.30 set --dehum 50 --power-saving on
    python3 ac_smart.py --ip 192.168.88.30 monitor --out energy.csv --interval 30
    python3 ac_smart.py --ip 192.168.88.30 echonet-test  # is ECHONET Lite answering on UDP 3610?

set reads the current state first and changes only what you pass, like
pymitsubishi's changeset flow; its frames are byte-identical to pymitsubishi's.
monitor polls power/energy and appends CSV or JSON lines (interval >= 10 s:
the adapter is a small device, be gentle).

Protocol (community-reverse-engineered, pymitsubishi/mac-577if-e):
request body <?xml ...?><ESV>base64(iv + AES-128-CBC(key, iso7816-padded XML))</ESV>
posted to http://<ip>/smart. Factory key is the literal "unregistered", NUL-padded.

Only run this on a trusted home network: /smart has no authentication.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import os
import socket
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

# --------------------------------------------------------------------------
# AES-128 (pure Python, FIPS-197). Tables are generated, not embedded.

def _gf_mul(a: int, b: int) -> int:
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p


def _gf_pow(a: int, n: int) -> int:
    r = 1
    while n:
        if n & 1:
            r = _gf_mul(r, a)
        a = _gf_mul(a, a)
        n >>= 1
    return r


def _rotl8(x: int, n: int) -> int:
    return ((x << n) | (x >> (8 - n))) & 0xFF


def _build_sboxes() -> tuple[list[int], list[int]]:
    sbox = [0] * 256
    inv = [0] * 256
    for i in range(256):
        x = 0 if i == 0 else _gf_pow(i, 254)  # multiplicative inverse
        s = x ^ _rotl8(x, 1) ^ _rotl8(x, 2) ^ _rotl8(x, 3) ^ _rotl8(x, 4) ^ 0x63
        sbox[i] = s
        inv[s] = i
    return sbox, inv


_SBOX, _INV_SBOX = _build_sboxes()
_RCON = [0x01]
for _i in range(1, 11):
    _RCON.append(_gf_mul(_RCON[-1], 2))


def _expand_key(key: bytes) -> list[list[int]]:
    """AES-128 key expansion: 16-byte key -> 11 round keys of 16 bytes."""
    assert len(key) == 16
    words = [list(key[i : i + 4]) for i in range(0, 16, 4)]
    for i in range(4, 44):
        t = list(words[i - 1])
        if i % 4 == 0:
            t = [_SBOX[t[1]] ^ _RCON[i // 4 - 1], _SBOX[t[2]], _SBOX[t[3]], _SBOX[t[0]]]
        words.append([a ^ b for a, b in zip(words[i - 4], t, strict=True)])
    flat = [b for word in words for b in word]
    return [flat[r * 16 : (r + 1) * 16] for r in range(11)]


def _add_round_key(s: list[int], rk: list[int]) -> None:
    for i in range(16):
        s[i] ^= rk[i]


def _shift_rows(s: list[int]) -> None:
    s[1], s[5], s[9], s[13] = s[5], s[9], s[13], s[1]
    s[2], s[6], s[10], s[14] = s[10], s[14], s[2], s[6]
    s[3], s[7], s[11], s[15] = s[15], s[3], s[7], s[11]


def _inv_shift_rows(s: list[int]) -> None:
    s[1], s[5], s[9], s[13] = s[13], s[1], s[5], s[9]
    s[2], s[6], s[10], s[14] = s[10], s[14], s[2], s[6]
    s[3], s[7], s[11], s[15] = s[7], s[11], s[15], s[3]


def _mix_columns(s: list[int]) -> None:
    for c in range(4):
        i = 4 * c
        a = s[i : i + 4]
        s[i] = _gf_mul(a[0], 2) ^ _gf_mul(a[1], 3) ^ a[2] ^ a[3]
        s[i + 1] = a[0] ^ _gf_mul(a[1], 2) ^ _gf_mul(a[2], 3) ^ a[3]
        s[i + 2] = a[0] ^ a[1] ^ _gf_mul(a[2], 2) ^ _gf_mul(a[3], 3)
        s[i + 3] = _gf_mul(a[0], 3) ^ a[1] ^ a[2] ^ _gf_mul(a[3], 2)


def _inv_mix_columns(s: list[int]) -> None:
    for c in range(4):
        i = 4 * c
        a = s[i : i + 4]
        s[i] = _gf_mul(a[0], 14) ^ _gf_mul(a[1], 11) ^ _gf_mul(a[2], 13) ^ _gf_mul(a[3], 9)
        s[i + 1] = _gf_mul(a[0], 9) ^ _gf_mul(a[1], 14) ^ _gf_mul(a[2], 11) ^ _gf_mul(a[3], 13)
        s[i + 2] = _gf_mul(a[0], 13) ^ _gf_mul(a[1], 9) ^ _gf_mul(a[2], 14) ^ _gf_mul(a[3], 11)
        s[i + 3] = _gf_mul(a[0], 11) ^ _gf_mul(a[1], 13) ^ _gf_mul(a[2], 9) ^ _gf_mul(a[3], 14)


def _sub_bytes(s: list[int], box: list[int]) -> None:
    for i in range(16):
        s[i] = box[s[i]]


def aes_encrypt_block(block: bytes, round_keys: list[list[int]]) -> bytes:
    s = list(block)
    _add_round_key(s, round_keys[0])
    for r in range(1, 10):
        _sub_bytes(s, _SBOX)
        _shift_rows(s)
        _mix_columns(s)
        _add_round_key(s, round_keys[r])
    _sub_bytes(s, _SBOX)
    _shift_rows(s)
    _add_round_key(s, round_keys[10])
    return bytes(s)


def aes_decrypt_block(block: bytes, round_keys: list[list[int]]) -> bytes:
    s = list(block)
    _add_round_key(s, round_keys[10])
    for r in range(9, 0, -1):
        _inv_shift_rows(s)
        _sub_bytes(s, _INV_SBOX)
        _add_round_key(s, round_keys[r])
        _inv_mix_columns(s)
    _inv_shift_rows(s)
    _sub_bytes(s, _INV_SBOX)
    _add_round_key(s, round_keys[0])
    return bytes(s)


def _cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    rk = _expand_key(key)
    out = bytearray()
    prev = iv
    for off in range(0, len(data), 16):
        block = bytes(a ^ b for a, b in zip(data[off : off + 16], prev, strict=True))
        prev = aes_encrypt_block(block, rk)
        out += prev
    return bytes(out)


def _cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    rk = _expand_key(key)
    out = bytearray()
    prev = iv
    for off in range(0, len(data), 16):
        block = data[off : off + 16]
        plain = aes_decrypt_block(block, rk)
        out += bytes(a ^ b for a, b in zip(plain, prev, strict=True))
        prev = block
    return bytes(out)


# --------------------------------------------------------------------------
# /smart protocol (pymitsubishi-compatible)

KEY = b"unregistered".ljust(16, b"\0")

_HEADERS = {
    "Content-Type": "text/plain;chrset=UTF-8",
    "Connection": "close",
    "Accept": "*/*",
    "User-Agent": (
        "KirigamineRemote/5.1.0 (jp.co.MitsubishiElectric.KirigamineRemote; "
        "build:3; iOS 17.5.1) Alamofire/5.9.1"
    ),
}


def _iso7816_pad(data: bytes) -> bytes:
    pad = 16 - (len(data) % 16)
    return data + b"\x80" + b"\x00" * (pad - 1)


def _iso7816_unpad(data: bytes) -> bytes:
    i = data.rfind(b"\x80")
    if i != -1 and all(b == 0 for b in data[i + 1 :]):
        return data[:i]
    return data.rstrip(b"\x00")


def encrypt_xml(plaintext: str) -> str:
    iv = os.urandom(16)
    ct = _cbc_encrypt(KEY, iv, _iso7816_pad(plaintext.encode("utf-8")))
    return base64.b64encode(iv + ct).decode("ascii")


def decrypt_xml(payload_b64: str) -> str:
    raw = base64.b64decode(payload_b64)
    plain = _iso7816_unpad(_cbc_decrypt(KEY, raw[:16], raw[16:]))
    return plain.decode("utf-8", errors="ignore")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects: the adapter answers /smart directly."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# The adapter is a fixed LAN device: a redirect or a proxy means something is
# intercepting the traffic, so both are hard failures, not things to follow.
_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),  # ignore http_proxy/https_proxy env vars
    _NoRedirect(),
)


def post_smart(ip: str, payload_xml: str, timeout: float = 5.0) -> str:
    body = f'<?xml version="1.0" encoding="UTF-8"?><ESV>{encrypt_xml(payload_xml)}</ESV>'
    req = urllib.request.Request(
        f"http://{ip}/smart", data=body.encode("utf-8"), headers=_HEADERS, method="POST"
    )
    with _OPENER.open(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8")
    root = ET.fromstring(text)
    if not root.text:
        raise RuntimeError("empty response from adapter")
    return decrypt_xml(root.text)


def calc_fcc(payload: bytes) -> int:
    return (0x100 - (sum(payload[0:20]) % 0x100)) % 0x100


def power_command_frame(on: bool) -> bytes:
    """Binary power set-frame (Controls.PowerOnOff | OutsideControl)."""
    # Byte-identical to pymitsubishi's GeneralStates.generate_general_command
    # with only the PowerOnOff control bit set: other fields carry the library
    # defaults (temp 22 C) and are ignored by the adapter anyway.
    cmd = bytearray(b"\x41\x01\x30\x10\x01") + b"\0" * 15
    cmd[5:7] = (0x0102).to_bytes(2, "big")  # PowerOnOff + OutsideControl
    cmd[7] = 0x01 if on else 0x00
    cmd[9] = 31 - 22
    cmd[18] = 0x80 + 22 * 2
    cmd[19] = 0x41
    return b"\xfc" + cmd + bytes([calc_fcc(cmd)])


_MODES = {0: "auto", 1: "heat", 2: "dry", 3: "cool", 7: "fan"}
_FAN = {0: "auto", 1: "S1", 2: "S2", 3: "S3", 5: "S4", 6: "full"}
_VANE_V = {0: "auto", 1: "V1", 2: "V2", 3: "V3", 4: "V4", 5: "V5", 7: "swing"}
_VANE_H = {
    0: "auto",
    1: "far-left",
    2: "left",
    3: "center",
    4: "right",
    5: "far-right",
    6: "left-center",
    7: "center-right",
    8: "left-right",
    9: "left-center-right",
    12: "swing",
}
_AUTO_MODE = {0: "off", 1: "switching", 2: "auto-heat", 3: "auto-cool"}


def _half_deg(raw: int) -> float | None:
    """(raw - 0x80) / 2 sensor encoding; -64 means 'not available'."""
    value = (raw - 0x80) * 0.5
    return None if value == -64 else value


def _general_lines(data: bytes) -> list[str]:
    power = "ON" if data[8] else "off"
    mode = _MODES.get(data[9] & 0x07, f"?{data[9] & 0x07}")
    temp = (data[16] - 0x80) / 2 if data[16] else 31 - data[10]
    parts = [
        f"power {power}",
        f"mode {mode}",
        f"set {temp:g} C",
        f"fan {_FAN.get(data[11], data[11])}",
        f"vane {_VANE_V.get(data[12], data[12])}/{_VANE_H.get(data[15] & 0x0F, data[15] & 0x0F)}",
    ]
    if data[9] & 0x08:
        parts.append("i-See on")
    if data[17]:
        parts.append(f"dehum {data[17]}")
    parts.append(f"power-saving {'on' if data[18] else 'off'}")
    lock = data[13]
    if lock:
        names = [name for bit, name in ((1, "power"), (2, "mode"), (4, "temp")) if lock & bit]
        unknown = lock & ~0b111
        if unknown:
            names.append(str(unknown))  # unknown bits: show the raw remainder
        parts.append(f"remote-lock {'+'.join(names)}")
    else:
        parts.append("remote-lock off")
    return ["AC: " + ", ".join(parts), "buzzer: command only (no state in status)"]


def _room_temp(data: bytes) -> float:
    room = _half_deg(data[11])
    return float(10 + data[8]) if room is None else room


def _sensor_lines(data: bytes) -> list[str]:
    room = _room_temp(data)
    parts = [f"room {room:g} C"]
    outside = _half_deg(data[10])
    if outside is not None:
        parts.append(f"outdoor {outside:g} C")
    runtime = int.from_bytes(data[15:19], "big")
    if runtime:
        parts.append(f"compressor runtime {runtime} min")
    return ["sensors: " + ", ".join(parts)]


def _error_lines(data: bytes) -> list[str]:
    code = int.from_bytes(data[9:11], "big")
    return ["error: none" if code in (0x0000, 0x8000) else f"error: code {code:#06x}"]


def _energy_fields(data: bytes) -> tuple[bool, int, float]:
    """(compressor running, instantaneous watts, cumulative kWh)."""
    return (
        bool(data[9]),
        int.from_bytes(data[10:12], "big"),
        int.from_bytes(data[12:14], "big") / 10,
    )


def _energy_lines(data: bytes) -> list[str]:
    operating, watts, kwh = _energy_fields(data)
    if not operating and watts == 0 and kwh == 0:
        return [
            "energy: idle, now 0 W, cumulative 0 kWh (all energy fields read zero;"
            " some units update them only while the compressor runs, and some"
            " adapters never report cumulative energy at all)"
        ]
    state = "running" if operating else "idle"
    return [f"energy: {state}, now {watts} W, lifetime {kwh:g} kWh"]


def _auto_lines(data: bytes) -> list[str]:
    return [f"auto mode: {_AUTO_MODE.get(data[10], data[10])}, demand level {data[9]}"]


_CODE_DECODERS = {
    0x02: (21, _general_lines),
    0x03: (21, _sensor_lines),
    0x04: (11, _error_lines),
    0x06: (14, _energy_lines),
    0x09: (11, _auto_lines),
}


def summarize(lsv_xml: str) -> str:
    """Human-readable summary of an LSV/CSV status reply."""
    lines: list[str] = []
    try:
        root = ET.fromstring(lsv_xml)
    except ET.ParseError:
        return lsv_xml
    for tag in ("MAC", "SERIAL", "APP_VER", "CONNECT", "ECHONET", "RSSI"):
        el = root.find(tag)
        if el is not None and el.text:
            lines.append(f"{tag}: {el.text}")
    for value in root.findall(".//CODE/VALUE"):
        try:
            data = bytes.fromhex(value.text or "")
        except ValueError:
            continue
        if len(data) < 6 or data[0] != 0xFC:
            continue
        decoder = _CODE_DECODERS.get(data[5])
        if decoder is not None and len(data) >= decoder[0]:
            lines.extend(decoder[1](data))
    return "\n".join(lines) if lines else lsv_xml


def cmd_status(ip: str, timeout: float) -> int:
    print(summarize(post_smart(ip, "<CSV></CSV>", timeout)))
    return 0


def cmd_power(ip: str, on: bool, timeout: float) -> int:
    frame = power_command_frame(on)
    reply = post_smart(ip, f"<CSV><CODE><VALUE>{frame.hex()}</VALUE></CODE></CSV>", timeout)
    print(summarize(reply))
    print(f"Power {'ON' if on else 'OFF'} command sent. The AC takes a few seconds to react;")
    print("re-run 'status' to confirm.")
    return 0


# --------------------------------------------------------------------------
# Control surface (byte-identical to pymitsubishi's command generators)

def _parse_codes(lsv_xml: str) -> dict[int, bytes]:
    """Map CODE type byte -> payload bytes from an LSV/CSV status reply."""
    codes: dict[int, bytes] = {}
    try:
        root = ET.fromstring(lsv_xml)
    except ET.ParseError:
        return codes
    for value in root.findall(".//CODE/VALUE"):
        try:
            data = bytes.fromhex(value.text or "")
        except ValueError:
            continue
        if len(data) >= 6 and data[0] == 0xFC:
            codes[data[5]] = data
    return codes


def parse_general(data: bytes) -> dict[str, object]:
    """Structured general-states parse; keys mirror pymitsubishi's GeneralStates."""
    return {
        "power": data[8],
        "mode": data[9] & 0x07,  # 0 = auto as reported; commands send 8 for auto
        "temp": (data[16] - 0x80) / 2 if data[16] else 31 - data[10],
        "fine": bool(data[16]),
        "fan": data[11],
        "vane_v": data[12],
        "remote_lock": data[13],
        "vane_h": data[15] & 0x0F,
        "dehum": data[17],
        "power_saving": data[18] > 0,
        "wind_break": data[19],
    }


# Controls bit values (general command), from pymitsubishi's Controls IntFlag.
_CTRL_POWER = 0x0100
_CTRL_MODE = 0x0200
_CTRL_TEMP = 0x0400
_CTRL_FAN = 0x0800
_CTRL_VANE_V = 0x1000
_CTRL_LOCK = 0x4000
_CTRL_VANE_H = 0x0001
_CTRL_OUTSIDE = 0x0002  # always set by pymitsubishi
# Controls08 bit values (extend08 command).
_CTRL08_DEHUM = 0x04
_CTRL08_PSAVE = 0x08
_CTRL08_BUZZER = 0x10


def general_command_frame(state: dict[str, object], controls: int) -> bytes:
    """General set-frame, byte-identical to pymitsubishi generate_general_command."""
    cmd = bytearray(b"\x41\x01\x30\x10\x01") + b"\0" * 15
    cmd[5:7] = (controls | _CTRL_OUTSIDE).to_bytes(2, "big")
    cmd[7] = int(state["power"])
    cmd[8] = int(state["mode"])
    cmd[9] = 31 - int(float(state["temp"]))
    cmd[10] = int(state["fan"])
    cmd[11] = int(state["vane_v"])
    cmd[15] = int(state["remote_lock"])
    cmd[17] = int(state["vane_h"])
    cmd[18] = 0x80 + int(float(state["temp"]) * 2) if state["fine"] else 0x00
    cmd[19] = 0x41
    return b"\xfc" + cmd + bytes([calc_fcc(cmd)])


def extend08_command_frame(state: dict[str, object], controls08: int) -> bytes:
    """Extend08 set-frame, byte-identical to pymitsubishi generate_extend08_command."""
    cmd = bytearray(b"\x41\x01\x30\x10\x08") + b"\0" * 15
    cmd[5] = controls08
    cmd[8] = int(state["dehum"]) if controls08 & _CTRL08_DEHUM else 0
    cmd[9] = 0x0A if state["power_saving"] else 0x00
    cmd[10] = int(state["wind_break"]) if controls08 & 0x20 else 0
    cmd[11] = 0x01 if controls08 & _CTRL08_BUZZER else 0
    return b"\xfc" + cmd + bytes([calc_fcc(cmd)])


_MODE_TO_BYTE = {"auto": 8, "heat": 1, "dry": 2, "cool": 3, "fan": 7}
_FAN_TO_BYTE = {"auto": 0, "s1": 1, "s2": 2, "s3": 3, "s4": 5, "full": 6}
_VANE_V_TO_BYTE = {"auto": 0, "v1": 1, "v2": 2, "v3": 3, "v4": 4, "v5": 5, "swing": 7}
_VANE_H_TO_BYTE = {
    "auto": 0,
    "far-left": 1,
    "left": 2,
    "center": 3,
    "right": 4,
    "far-right": 5,
    "left-center": 6,
    "center-right": 7,
    "left-right": 8,
    "left-center-right": 9,
    "swing": 12,
}
_LOCK_BITS = {"power": 1, "mode": 2, "temp": 4}


def _parse_remote_lock(text: str) -> int:
    if text in ("unlock", "none", "off"):
        return 0
    try:
        bits = [_LOCK_BITS[part.strip()] for part in text.split(",")]
    except KeyError as exc:
        raise ValueError(
            f"bad --remote-lock value {exc}; use unlock or a comma list of power,mode,temp"
        ) from None
    value = 0
    for bit in bits:
        value |= bit
    return value


def _send_frame(ip: str, frame: bytes, timeout: float) -> str:
    return post_smart(ip, f"<CSV><CODE><VALUE>{frame.hex()}</VALUE></CODE></CSV>", timeout)


def cmd_set(args: argparse.Namespace) -> int:
    lsv = post_smart(args.ip, "<CSV></CSV>", args.timeout)
    data = _parse_codes(lsv).get(0x02)
    if data is None or len(data) < 21:
        raise RuntimeError("status reply carried no general-states payload; cannot set safely")
    state = parse_general(data)
    controls = 0
    controls08 = 0
    if args.mode is not None:
        state["mode"] = _MODE_TO_BYTE[args.mode]
        controls |= _CTRL_MODE
    if args.temp is not None:
        temp = args.temp
        if not 16.0 <= temp <= 31.0 or (temp * 2) != int(temp * 2):
            raise ValueError("--temp must be 16.0-31.0 in 0.5 C steps")
        state["temp"] = temp
        state["fine"] = True
        controls |= _CTRL_TEMP
    if args.fan is not None:
        state["fan"] = _FAN_TO_BYTE[args.fan]
        controls |= _CTRL_FAN
    if args.vane_v is not None:
        state["vane_v"] = _VANE_V_TO_BYTE[args.vane_v]
        controls |= _CTRL_VANE_V
    if args.vane_h is not None:
        state["vane_h"] = _VANE_H_TO_BYTE[args.vane_h]
        controls |= _CTRL_VANE_H
    if args.remote_lock is not None:
        state["remote_lock"] = _parse_remote_lock(args.remote_lock)
        controls |= _CTRL_LOCK
    if args.dehum is not None:
        if not 0 <= args.dehum <= 255:
            raise ValueError("--dehum must be 0-255 (the raw level 'status' reports)")
        state["dehum"] = args.dehum
        controls08 |= _CTRL08_DEHUM
    if args.power_saving is not None:
        state["power_saving"] = args.power_saving == "on"
        controls08 |= _CTRL08_PSAVE
    if args.buzzer:
        controls08 |= _CTRL08_BUZZER
    if not controls and not controls08:
        raise ValueError("nothing to set: pass at least one option (see set --help)")
    reply = ""
    if controls:
        reply = _send_frame(args.ip, general_command_frame(state, controls), args.timeout)
    if controls08:
        reply = _send_frame(args.ip, extend08_command_frame(state, controls08), args.timeout)
    print(summarize(reply))
    print("Command sent. The AC takes a few seconds to react; re-run 'status' to confirm.")
    return 0


_MONITOR_COLUMNS = (
    "timestamp",
    "operating",
    "power_w",
    "energy_kwh",
    "ac_power",
    "mode",
    "set_temp_c",
    "room_temp_c",
)


def _monitor_sample(lsv_xml: str) -> dict[str, object]:
    codes = _parse_codes(lsv_xml)
    row: dict[str, object] = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    energy = codes.get(0x06)
    if energy is not None and len(energy) >= 14:
        operating, watts, kwh = _energy_fields(energy)
        row.update(operating=int(operating), power_w=watts, energy_kwh=f"{kwh:g}")
    general = codes.get(0x02)
    if general is not None and len(general) >= 21:
        state = parse_general(general)
        row.update(
            ac_power="on" if state["power"] else "off",
            mode=_MODES.get(int(state["mode"]), state["mode"]),
            set_temp_c=f"{float(state['temp']):g}",
        )
    sensor = codes.get(0x03)
    if sensor is not None and len(sensor) >= 21:
        row["room_temp_c"] = f"{_room_temp(sensor):g}"
    return row


def cmd_monitor(args: argparse.Namespace, sleep: object = None) -> int:
    if args.interval < 10:
        raise ValueError("--interval below 10 s is too aggressive for the adapter")
    if sleep is None:
        sleep = time.sleep
    fmt = args.format
    if fmt is None:
        fmt = "json" if args.out.endswith((".json", ".jsonl")) else "csv"
    write_header = fmt == "csv" and (not os.path.exists(args.out) or os.path.getsize(args.out) == 0)
    samples = 0
    with open(args.out, "a", encoding="utf-8") as fh:
        if write_header:
            fh.write(",".join(_MONITOR_COLUMNS) + "\n")
        try:
            while True:
                try:
                    row = _monitor_sample(post_smart(args.ip, "<CSV></CSV>", args.timeout))
                except (OSError, RuntimeError, ET.ParseError) as exc:
                    print(f"poll failed: {exc}", file=sys.stderr)
                else:
                    if fmt == "json":
                        fh.write(json.dumps(row, separators=(",", ":")) + "\n")
                    else:
                        fh.write(",".join(str(row.get(col, "")) for col in _MONITOR_COLUMNS) + "\n")
                    fh.flush()
                    print(" ".join(f"{k}={v}" for k, v in row.items()))
                    samples += 1
                    if args.count and samples >= args.count:
                        return 0
                sleep(args.interval)
        except KeyboardInterrupt:
            print(f"stopped after {samples} samples", file=sys.stderr)
    return 0


# --------------------------------------------------------------------------
# ECHONET Lite probe (UDP 3610)

_ECHONET_PORT = 3610
_MULTICAST_GROUP = "224.0.23.0"  # ECHONET Lite IPv4 multicast group (spec)
# Get operation status (EPC 0x80) from the home air conditioner (0x013001):
_GET_AC_POWER = bytes.fromhex("1081000105ff0101300162018000")
# Get self-node instance list (EPC 0xD6) from the node profile (0x0EF001):
_GET_NODE_LIST = bytes.fromhex("1081000205ff010ef0016201d600")


def _echonet_listen(sock: socket.socket, deadline: float, out: list[str]) -> None:
    import time as _time

    while True:
        remaining = deadline - _time.monotonic()
        if remaining <= 0:
            return
        sock.settimeout(remaining)
        try:
            data, (addr, _port) = sock.recvfrom(4096)
        except TimeoutError:
            return
        if data in (_GET_AC_POWER, _GET_NODE_LIST):
            continue  # multicast loopback echoing our own probe
        if len(data) >= 12 and data[0] == 0x10 and data[1] == 0x81:
            note = ""
            if data[10] == 0x72 and len(data) >= 14 and data[12] == 0x80 and data[14:15]:
                note = f" (power {'ON' if data[14] == 0x30 else 'off'})"
            out.append(f"reply from {addr}: {data.hex()}{note}")
        else:
            out.append(f"non-ECHONET datagram from {addr}: {data.hex()}")


def cmd_echonet_test(ip: str, timeout: float) -> int:
    """Probe ECHONET Lite: unicast Get, then multicast node-profile discovery.

    These adapters send replies to UDP port 3610 (not to the request's source
    port), so the socket must be bound to 3610 - an ephemeral source port sees
    silence even when ECHONET is on.
    """
    import time as _time

    out: list[str] = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", _ECHONET_PORT))
    except OSError as exc:
        sock.close()
        print(f"Cannot bind UDP 3610: {exc}", file=sys.stderr)
        print("Something else is already listening on 3610 (Home Assistant? Homebridge?).",
              file=sys.stderr)
        return 1
    try:
        sock.sendto(_GET_AC_POWER, (ip, _ECHONET_PORT))
        _echonet_listen(sock, _time.monotonic() + timeout, out)
        if out:
            print("\n".join(out))
            print("ECHONET Lite is ENABLED and answering.")
            return 0
        print(f"No unicast reply within {timeout}s; trying multicast discovery...")
        with contextlib.suppress(OSError):
            sock.setsockopt(
                socket.IPPROTO_IP,
                socket.IP_ADD_MEMBERSHIP,
                socket.inet_aton(_MULTICAST_GROUP) + socket.inet_aton("0.0.0.0"),
            )
        try:
            sock.sendto(_GET_NODE_LIST, (_MULTICAST_GROUP, _ECHONET_PORT))
        except OSError as exc:
            print(f"multicast send failed: {exc}")
        _echonet_listen(sock, _time.monotonic() + timeout, out)
        if out:
            print("\n".join(out))
            print("ECHONET Lite devices answered the multicast probe (see above).")
            return 0
        print("No ECHONET reply on unicast or multicast.")
        print("If the adapter reports ECHONET: ON via 'status', check for client")
        print("isolation / UDP filtering on the Wi-Fi, or a firewall on this machine.")
        return 1
    finally:
        sock.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ip", help="adapter IP on the local network")
    parser.add_argument("--timeout", type=float, default=5.0, help="seconds (default 5)")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        # Repeat the global options on every subcommand so they parse in any
        # position. SUPPRESS defaults keep them from clobbering values the
        # main parser already consumed.
        p.add_argument("--ip", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        p.add_argument("--timeout", type=float, default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    for name in ("status", "on", "off", "echonet-test"):
        common(sub.add_parser(name))

    p_set = sub.add_parser("set", help="change settings; everything not passed stays as-is")
    common(p_set)
    p_set.add_argument("--mode", choices=sorted(_MODE_TO_BYTE))
    p_set.add_argument("--temp", type=float, help="setpoint 16.0-31.0 in 0.5 C steps")
    p_set.add_argument("--fan", choices=sorted(_FAN_TO_BYTE))
    p_set.add_argument("--vane-v", choices=sorted(_VANE_V_TO_BYTE), help="vertical vane")
    p_set.add_argument("--vane-h", choices=sorted(_VANE_H_TO_BYTE), help="horizontal vane")
    p_set.add_argument(
        "--dehum", type=int, help="dehumidifier level 0-255, raw as 'status' reports"
    )
    p_set.add_argument("--power-saving", choices=["on", "off"])
    p_set.add_argument(
        "--remote-lock", metavar="unlock|power,mode,temp", help="remote-control lock"
    )
    p_set.add_argument("--buzzer", action="store_true", help="beep the indoor unit")

    p_mon = sub.add_parser("monitor", help="poll power/energy and append CSV or JSON lines")
    common(p_mon)
    p_mon.add_argument(
        "--out", required=True, help="log file (.json/.jsonl = JSON lines, else CSV)"
    )
    p_mon.add_argument(
        "--interval", type=float, default=30.0, help="seconds between polls (min 10, default 30)"
    )
    p_mon.add_argument(
        "--count", type=int, default=0, help="stop after N samples (default: run until Ctrl-C)"
    )
    p_mon.add_argument("--format", choices=["csv", "json"], help="default: from --out extension")

    args = parser.parse_args(argv)
    if not args.ip:
        parser.error("--ip is required")
    try:
        if args.command == "status":
            return cmd_status(args.ip, args.timeout)
        if args.command == "echonet-test":
            return cmd_echonet_test(args.ip, args.timeout)
        if args.command == "set":
            return cmd_set(args)
        if args.command == "monitor":
            return cmd_monitor(args)
        return cmd_power(args.ip, args.command == "on", args.timeout)
    except (OSError, RuntimeError, ValueError, ET.ParseError) as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        print(
            "Check the IP and that this machine is on the same network as the adapter.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
