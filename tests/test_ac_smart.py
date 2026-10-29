"""Tests for scripts/ac_smart.py (loaded as a standalone file, stdlib only)."""

from __future__ import annotations

import importlib.util
import socket
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
        assert "AC: power ON, mode cool, set 24 C" in out
        assert "sensors: room 23 C" in out
        assert "ECHONET: ON" in out
        assert "APP_VER: 40.00" in out

    def test_unparseable_passthrough(self) -> None:
        assert ac_smart.summarize("not xml") == "not xml"

    # Real CODE frames captured from a MAC-577IF-E (app v40.00) - state data
    # only, no identifiers. pymitsubishi parses them to: power off, dry, 25 C,
    # fan auto, vane V2/left, room 23.5 C, no error, energy idle, auto off.
    REAL_CODES = [
        "fc620130100200000002060002000002b2000000009d",
        "fc620130100300000d0000af0000000000000000009e",
        "fc6201301004000000800000000000000000000000d9",
        "fc620130100500000000000000000000000000000058",
        "fc620130100600000300000000000000000000000054",
        "fc620130100900000000000000000000000000000054",
    ]

    def test_real_capture_matches_pymitsubishi(self) -> None:
        lsv = "<LSV>" + "".join(
            f"<CODE><VALUE>{c}</VALUE></CODE>" for c in self.REAL_CODES
        ) + "</LSV>"
        out = ac_smart.summarize(lsv)
        assert "AC: power off, mode dry, set 25 C, fan auto, vane V2/left" in out
        assert "sensors: room 23.5 C" in out
        assert "error: none" in out
        assert "energy: idle, now 0 W, lifetime 0 kWh" in out
        assert "auto mode: off, demand level 0" in out

    def _code(self, kind: int, fill: dict[int, int], length: int = 22) -> str:
        data = bytearray(length)
        data[0:6] = bytes([0xFC, 0x62, 0x01, 0x30, 0x10, kind])
        for pos, val in fill.items():
            data[pos] = val
        return data.hex()

    def test_sensor_outdoor_and_runtime(self) -> None:
        code = self._code(0x03, {8: 13, 10: 0x80 + 40, 11: 0x80 + 47, 17: 5})
        out = ac_smart.summarize(f"<LSV><CODE><VALUE>{code}</VALUE></CODE></LSV>")
        assert "sensors: room 23.5 C, outdoor 20 C" in out
        assert "compressor runtime 1280 min" in out

    def test_error_code_shown(self) -> None:
        code = self._code(0x04, {9: 0x6A, 10: 0x30}, length=21)
        out = ac_smart.summarize(f"<LSV><CODE><VALUE>{code}</VALUE></CODE></LSV>")
        assert "error: code 0x6a30" in out

    def test_energy_watts(self) -> None:
        code = self._code(0x06, {9: 1, 11: 0x96, 13: 0x64})
        out = ac_smart.summarize(f"<LSV><CODE><VALUE>{code}</VALUE></CODE></LSV>")
        assert "energy: running, now 150 W, lifetime 10 kWh" in out

    def test_general_extras(self) -> None:
        code = self._code(0x02, {8: 1, 9: 0x0B, 10: 9, 11: 3, 12: 7, 16: 0xAC, 17: 50, 18: 1})
        out = ac_smart.summarize(f"<LSV><CODE><VALUE>{code}</VALUE></CODE></LSV>")
        assert "mode cool" in out and "i-See on" in out
        assert "dehum 50" in out and "power-saving" in out
        assert "fan S3" in out and "vane swing/auto" in out


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
        assert "AC: power ON, mode cool" in out

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


class TestEchonetTest:
    def _responder(self) -> threading.Thread:
        def serve() -> None:
            r = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            r.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            r.bind(("127.0.0.1", 3610))
            r.settimeout(5)
            try:
                r.recvfrom(4096)
            finally:
                r.close()
            # Reply like the real adapter: to port 3610, from an ephemeral port.
            w = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            w.sendto(bytes.fromhex("1081000101300105ff017201800130"), ("127.0.0.1", 3610))
            w.close()

        t = threading.Thread(target=serve, daemon=True)
        t.start()
        return t

    def test_enabled_reply(self, capsys: pytest.CaptureFixture[str]) -> None:
        import time

        self._responder()
        time.sleep(0.1)
        rc = ac_smart.main(["--ip", "127.0.0.1", "--timeout", "2", "echonet-test"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "ECHONET Lite is ENABLED" in out
        assert "power ON" in out

    def test_no_reply(self, capsys: pytest.CaptureFixture[str]) -> None:
        # Drain any stale datagram left on 3610 by the previous test's responder.
        drain = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        drain.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        drain.bind(("", 3610))
        drain.settimeout(0.05)
        try:
            while True:
                drain.recvfrom(4096)
        except TimeoutError:
            pass
        drain.close()
        rc = ac_smart.main(["--ip", "127.0.0.1", "--timeout", "0.3", "echonet-test"])
        assert rc == 1
        assert "No ECHONET reply" in capsys.readouterr().out
