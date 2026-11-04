"""AES-128-CBC (pure Python, FIPS-197) for the adapter's /smart payloads.

The MAC-577IF-E /smart endpoint wraps service commands in AES-128-CBC with
ISO/IEC 7816-4 padding, base64-encoded inside an ``<ESV>`` element. Tables are
generated, not embedded. Ported from ``scripts/ac_smart.py`` so the shipped
CLI can read the adapter's HTTP-reported status flag (issue #70); the two
implementations must stay byte-compatible, so edit them together.
"""

from __future__ import annotations

import base64
import os


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

def _iso7816_pad(data: bytes) -> bytes:
    pad = 16 - (len(data) % 16)
    return data + b"\x80" + b"\x00" * (pad - 1)


def _iso7816_unpad(data: bytes) -> bytes:
    i = data.rfind(b"\x80")
    if i != -1 and all(b == 0 for b in data[i + 1 :]):
        return data[:i]
    return data.rstrip(b"\x00")


def encrypt_xml(plaintext: str, key: bytes) -> str:
    iv = os.urandom(16)
    ct = _cbc_encrypt(key, iv, _iso7816_pad(plaintext.encode("utf-8")))
    return base64.b64encode(iv + ct).decode("ascii")


def decrypt_xml(payload_b64: str, key: bytes) -> str:
    raw = base64.b64decode(payload_b64)
    plain = _iso7816_unpad(_cbc_decrypt(key, raw[:16], raw[16:]))
    return plain.decode("utf-8", errors="ignore")
