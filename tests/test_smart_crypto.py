"""Known-answer tests for the /smart AES crypto ported from scripts/ac_smart.py (#70)."""

from __future__ import annotations

import re

from mitsubishi_echonet import smart, smart_crypto

# FIPS-197 Appendix C.1 AES-128 example.
FIPS197_KEY = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
FIPS197_PLAINTEXT = bytes.fromhex("00112233445566778899aabbccddeeff")
FIPS197_CIPHERTEXT = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")


def _esv_body(payload: str) -> str:
    match = re.search(r"<ESV>([^<]+)</ESV>", payload)
    assert match is not None
    return match.group(1)


class TestAesBlock:
    def test_fips197_encrypt(self) -> None:
        round_keys = smart_crypto._expand_key(FIPS197_KEY)
        assert smart_crypto.aes_encrypt_block(FIPS197_PLAINTEXT, round_keys) == FIPS197_CIPHERTEXT

    def test_fips197_decrypt(self) -> None:
        round_keys = smart_crypto._expand_key(FIPS197_KEY)
        assert smart_crypto.aes_decrypt_block(FIPS197_CIPHERTEXT, round_keys) == FIPS197_PLAINTEXT


class TestXmlCrypto:
    def test_round_trip(self) -> None:
        plain = "<CSV><CONNECT>ON</CONNECT><ECHONET>ON</ECHONET></CSV>"
        encrypted = smart_crypto.encrypt_xml(plain, smart.ADAPTER_KEY)
        assert smart_crypto.decrypt_xml(encrypted, smart.ADAPTER_KEY) == plain

    def test_shipped_enable_payload_decrypts_to_echonet_on(self) -> None:
        # Production known-answer vector: the constant shipped in smart.py.
        plain = smart_crypto.decrypt_xml(_esv_body(smart.ENABLE_ECHONET_PAYLOAD), smart.ADAPTER_KEY)
        assert "<ECHONET>ON</ECHONET>" in plain

    def test_shipped_disable_payload_decrypts_to_echonet_off(self) -> None:
        plain = smart_crypto.decrypt_xml(
            _esv_body(smart.DISABLE_ECHONET_PAYLOAD), smart.ADAPTER_KEY
        )
        assert "<ECHONET>OFF</ECHONET>" in plain
