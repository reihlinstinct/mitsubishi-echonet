"""Tests for scripts/ac_smart.py (loaded as a standalone file, stdlib only)."""

from __future__ import annotations

import importlib.util
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ac_smart.py"
_spec = importlib.util.spec_from_file_location("ac_smart", _SCRIPT)
assert _spec and _spec.loader
ac_smart = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ac_smart)


class TestAes:
    KEY128 = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    PT = bytes.fromhex("00112233445566778899aabbccddeeff")
    CT = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")  # FIPS-197 C.1

    def test_fips197_encrypt(self) -> None:
        assert ac_smart.aes_encrypt_block(self.PT, ac_smart._expand_key(self.KEY128)) == self.CT

    def test_fips197_decrypt(self) -> None:
        assert ac_smart.aes_decrypt_block(self.CT, ac_smart._expand_key(self.KEY128)) == self.PT

    def test_sbox_spot_values(self) -> None:
        assert ac_smart._SBOX[0x00] == 0x63
        assert ac_smart._SBOX[0x53] == 0xED
        assert ac_smart._INV_SBOX[0x63] == 0x00

    def test_cbc_roundtrip(self) -> None:
        import os

        iv, data = os.urandom(16), os.urandom(100)
        ct = ac_smart._cbc_encrypt(ac_smart.KEY, iv, ac_smart._iso7816_pad(data))
        assert ac_smart._iso7816_unpad(ac_smart._cbc_decrypt(ac_smart.KEY, iv, ct)) == data


class TestProtocolCrypto:
    # The published enable payload (already public in this repo's smart.py):
    # fixed IV, factory key, known plaintext.
    ENABLE_B64 = (
        "7WVvmfhMYzGVi70nyFhmKEy9Jo3Hg3994vi9y1kEgDFWd/1ch9RWDUgY4HgsvMHFvP93fQ30"
        "AvEJCNcd0GTwPID0F8V5eyMVj/qAQCXFqYrRtJh8MIpm2/h7jZ2SsPj0"
    )

    def test_decrypt_known_enable_blob(self) -> None:
        plain = ac_smart.decrypt_xml(self.ENABLE_B64)
        assert "<CONNECT>ON</CONNECT>" in plain
        assert "<ECHONET>ON</ECHONET>" in plain

    def test_encrypt_decrypt_roundtrip(self) -> None:
        text = "<CSV><CODE><VALUE>fc4101</VALUE></CODE></CSV>"
        assert ac_smart.decrypt_xml(ac_smart.encrypt_xml(text)) == text

    def test_padding_edge_exact_block(self) -> None:
        data = b"x" * 32
        padded = ac_smart._iso7816_pad(data)
        assert len(padded) == 48  # always adds a full block when aligned
        assert ac_smart._iso7816_unpad(padded) == data


class TestPowerFrame:
    # Byte-exact match with pymitsubishi's GeneralStates.generate_general_command
    # (Controls.PowerOnOff), verified against pymitsubishi 0.6.1.
    def test_power_on(self) -> None:
        assert ac_smart.power_command_frame(True).hex() == (
            "fc410130100101020100090000000000000000ac4183"
        )

    def test_power_off(self) -> None:
        assert ac_smart.power_command_frame(False).hex() == (
            "fc410130100101020000090000000000000000ac4184"
        )

    def test_fcc(self) -> None:
        assert ac_smart.calc_fcc(bytes.fromhex("410130100101020100090000000000000000ac41")) == 0x83


def _general_states(power: int, mode: int, fine_c: float) -> str:
    data = bytearray(22)
    data[0:6] = b"\xfc\x62\x01\x30\x10\x02"
    data[8] = power
    data[9] = mode
    data[10] = 31 - int(fine_c)
    data[16] = 0x80 + int(fine_c * 2)
    return data.hex()


def _sensor_states(room_c: int) -> str:
    data = bytearray(22)
    data[0:6] = b"\xfc\x62\x01\x30\x10\x03"
    data[8] = room_c - 10
    return data.hex()


CANNED_LSV = (
    "<LSV><CONNECT>ON</CONNECT><ECHONET>ON</ECHONET><APP_VER>40.00</APP_VER>"
    f"<CODE><VALUE>{_general_states(1, 3, 24.0)}</VALUE>"
    f"<VALUE>{_sensor_states(23)}</VALUE></CODE></LSV>"
)


class TestSummarize:
    def test_parses_states(self) -> None:
        out = ac_smart.summarize(CANNED_LSV)
        assert "AC power: ON, mode: cool, set temp: 24 C" in out
        assert "room temp: 23 C" in out
        assert "ECHONET: ON" in out
        assert "APP_VER: 40.00" in out

    def test_unparseable_passthrough(self) -> None:
        assert ac_smart.summarize("not xml") == "not xml"


@pytest.fixture()
def mock_adapter():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"])).decode()
            b64 = body.split("<ESV>")[1].split("</ESV>")[0]
            assert ac_smart.decrypt_xml(b64).startswith("<CSV>")  # request is valid
            resp = f'<?xml version="1.0"?><ESV>{ac_smart.encrypt_xml(CANNED_LSV)}</ESV>'
            self.send_response(200)
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp.encode())

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{server.server_port}"
    server.shutdown()


class TestEndToEnd:
    def test_status(self, mock_adapter: str, capsys: pytest.CaptureFixture[str]) -> None:
        assert ac_smart.main(["--ip", mock_adapter, "status"]) == 0
        out = capsys.readouterr().out
        assert "AC power: ON, mode: cool" in out

    def test_power_on(self, mock_adapter: str, capsys: pytest.CaptureFixture[str]) -> None:
        assert ac_smart.main(["--ip", mock_adapter, "on"]) == 0
        assert "Power ON command sent" in capsys.readouterr().out

    def test_unreachable(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert ac_smart.main(["--ip", "127.0.0.1:1", "--timeout", "0.2", "status"]) == 1
        assert "Failed" in capsys.readouterr().err


class TestTransportHardening:
    def test_redirect_not_followed(self, capsys: pytest.CaptureFixture[str]) -> None:
        hits: list[str] = []

        class Target(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                hits.append("target")

            def log_message(self, *args: object) -> None:
                pass

        class Redirector(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{target_port}/smart")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args: object) -> None:
                pass

        target = HTTPServer(("127.0.0.1", 0), Target)
        target_port = target.server_port
        redirector = HTTPServer(("127.0.0.1", 0), Redirector)
        threading.Thread(target=target.serve_forever, daemon=True).start()
        threading.Thread(target=redirector.serve_forever, daemon=True).start()
        try:
            rc = ac_smart.main(["--ip", f"127.0.0.1:{redirector.server_port}", "status"])
            assert rc == 1
            assert hits == []
            assert "Failed" in capsys.readouterr().err
        finally:
            target.shutdown()
            redirector.shutdown()

    def test_proxy_env_ignored(self, mock_adapter: str, monkeypatch: pytest.MonkeyPatch) -> None:
        # A dead proxy in the env must not capture or break the request.
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
        assert ac_smart.main(["--ip", mock_adapter, "status"]) == 0
