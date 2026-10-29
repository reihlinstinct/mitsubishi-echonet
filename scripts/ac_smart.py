#!/usr/bin/env python3
"""ac_smart.py - local control of a Mitsubishi MAC-577IF-E(-class) Wi-Fi adapter.

Talks to the adapter's unauthenticated local /smart HTTP endpoint (the same
channel the MELCloud Home app uses on the LAN). No cloud, no dependencies:
Python 3.10+ standard library only.

Usage:
    python3 ac_smart.py --ip 192.168.88.30 status   # read-only: power, mode, temps
    python3 ac_smart.py --ip 192.168.88.30 on       # power on (keeps last mode/temp)
    python3 ac_smart.py --ip 192.168.88.30 off      # power off

Protocol (community-reverse-engineered, pymitsubishi/mac-577if-e):
request body <?xml ...?><ESV>base64(iv + AES-128-CBC(key, iso7816-padded XML))</ESV>
posted to http://<ip>/smart. Factory key is the literal "unregistered", NUL-padded.

Only run this on a trusted home network: /smart has no authentication.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
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
        if len(data) >= 21 and data[0] == 0xFC and data[5] == 0x02:
            power = "ON" if data[8] else "off"
            mode = _MODES.get(data[9] & 0x07, f"?{data[9] & 0x07}")
            temp = (data[16] - 0x80) / 2 if data[16] else 31 - data[10]
            lines.append(f"AC power: {power}, mode: {mode}, set temp: {temp:g} C")
        elif len(data) >= 21 and data[0] == 0xFC and data[5] == 0x03:
            lines.append(f"room temp: {10 + data[8]} C")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ip", required=True, help="adapter IP on the local network")
    parser.add_argument("--timeout", type=float, default=5.0, help="seconds (default 5)")
    parser.add_argument("command", choices=["status", "on", "off"])
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            return cmd_status(args.ip, args.timeout)
        return cmd_power(args.ip, args.command == "on", args.timeout)
    except (OSError, RuntimeError, ET.ParseError) as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        print(
            "Check the IP and that this machine is on the same network as the adapter.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
