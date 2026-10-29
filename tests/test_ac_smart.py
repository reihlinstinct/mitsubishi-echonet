"""Tests for scripts/ac_smart.py (loaded as a standalone file, stdlib only)."""

from __future__ import annotations

import importlib.util
import json
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
        assert "energy: idle, now 0 W, cumulative 0 kWh" in out
        assert "some adapters never report cumulative energy" in out
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

    def test_remote_lock_words(self) -> None:
        code = self._code(0x02, {8: 1, 13: 3})
        out = ac_smart.summarize(f"<LSV><CODE><VALUE>{code}</VALUE></CODE></LSV>")
        assert "remote-lock power+mode" in out
        code = self._code(0x02, {8: 1, 13: 4})
        out = ac_smart.summarize(f"<LSV><CODE><VALUE>{code}</VALUE></CODE></LSV>")
        assert "remote-lock temp" in out

    def test_remote_lock_off_shown_explicitly(self) -> None:
        code = self._code(0x02, {8: 1})
        out = ac_smart.summarize(f"<LSV><CODE><VALUE>{code}</VALUE></CODE></LSV>")
        assert "remote-lock off" in out
        assert "remote-lock 0" not in out


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


class TestControlFrames:
    """Byte-identical frames vs pymitsubishi 0.6.1 command generators.

    Expected hex generated with pymitsubishi's GeneralStates/generate_general_command
    and generate_extend08_command from the same logical state (power off, mode dry,
    set 25.0 C, fan auto, vane V2/left, unlocked - the state the real unit reported).
    """

    STATE = {
        "power": 0,
        "mode": 2,
        "temp": 25.0,
        "fine": True,
        "fan": 0,
        "vane_v": 2,
        "remote_lock": 0,
        "vane_h": 2,
        "dehum": 0,
        "power_saving": False,
        "wind_break": 0,
    }

    def frame(self, controls, **over):
        state = dict(self.STATE)
        state.update(over)
        return ac_smart.general_command_frame(state, controls).hex()

    def frame08(self, controls08, **over):
        state = dict(self.STATE)
        state.update(over)
        return ac_smart.extend08_command_frame(state, controls08).hex()

    def test_mode_cool(self):
        assert self.frame(0x0200, mode=3) == "fc410130100102020003060002000000000002b24179"

    def test_mode_auto_sent_as_8(self):
        assert self.frame(0x0200, mode=8) == "fc410130100102020008060002000000000002b24174"

    def test_temp_half_degree(self):
        assert self.frame(0x0400, temp=24.5) == "fc410130100104020002070002000000000002b14178"

    def test_temp_min_16(self):
        assert self.frame(0x0400, temp=16.0) == "fc4101301001040200020f0002000000000002a04181"

    def test_fan_s3(self):
        assert self.frame(0x0800, fan=3) == "fc410130100108020002060302000000000002b24171"

    def test_fan_full(self):
        assert self.frame(0x0800, fan=6) == "fc410130100108020002060602000000000002b2416e"

    def test_vane_v_swing(self):
        assert self.frame(0x1000, vane_v=7) == "fc410130100110020002060007000000000002b24167"

    def test_vane_h_far_right(self):
        assert self.frame(0x0001, vane_h=5) == "fc410130100100030002060002000000000005b24178"

    def test_vane_h_swing(self):
        assert self.frame(0x0001, vane_h=12) == "fc41013010010003000206000200000000000cb24171"

    def test_remote_lock_power_plus_mode(self):
        assert self.frame(0x4000, remote_lock=3) == "fc410130100140020002060002000000030002b24139"

    def test_combo_power_mode_temp_fan(self):
        frame = self.frame(0x0100 | 0x0200 | 0x0400 | 0x0800, power=1, mode=1, temp=21.5, fan=2)
        assert frame == "fc41013010010f0201010a0202000000000002ab416e"

    def test_dehum(self):
        assert self.frame08(0x04, dehum=50) == "fc410130100804000032000000000000000000000040"

    def test_power_saving_on(self):
        assert (
            self.frame08(0x08, power_saving=True) == "fc4101301008080000000a0000000000000000000064"
        )

    def test_power_saving_off(self):
        assert (
            self.frame08(0x08, power_saving=False) == "fc41013010080800000000000000000000000000006e"
        )

    def test_buzzer(self):
        assert self.frame08(0x10) == "fc410130100810000000000001000000000000000065"

    def test_dehum_and_power_saving(self):
        assert self.frame08(0x04 | 0x08, dehum=60, power_saving=True) == (
            "fc41013010080c00003c0a0000000000000000000024"
        )

    def test_remote_lock_parse(self):
        assert ac_smart._parse_remote_lock("unlock") == 0
        assert ac_smart._parse_remote_lock("power,temp") == 5
        with pytest.raises(ValueError):
            ac_smart._parse_remote_lock("bogus")


class TestSetCommand:
    """cmd_set against the mock adapter: fetches state first, then sends
    frames byte-identical to pymitsubishi for that fetched state (power on,
    cool, 24.0 C, fan/vanes auto - the CANNED_LSV fixture)."""

    EXPECTED = {
        ("--mode", "heat"): "fc410130100102020101070000000000000000b0417f",
        ("--fan", "s3"): "fc410130100108020103070300000000000000b04174",
        ("--temp", "22.5"): "fc410130100104020103090000000000000000ad417c",
    }

    def _sent_frames(self, mock_adapter: str, argv: list[str]) -> list[str]:
        frames: list[str] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers["Content-Length"])).decode()
                b64 = body.split("<ESV>")[1].split("</ESV>")[0]
                plain = ac_smart.decrypt_xml(b64)
                if "<CODE>" in plain:
                    frames.append(plain.split("<VALUE>")[1].split("</VALUE>")[0])
                resp = f'<?xml version="1.0"?><ESV>{ac_smart.encrypt_xml(CANNED_LSV)}</ESV>'
                self.send_response(200)
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp.encode())

            def log_message(self, *args: object) -> None:
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            rc = ac_smart.main(["--ip", f"127.0.0.1:{server.server_port}", *argv])
        finally:
            server.shutdown()
        assert rc == 0
        return frames

    def test_each_general_option_byte_identical(self, mock_adapter: str) -> None:
        for argv, expected in self.EXPECTED.items():
            frames = self._sent_frames(mock_adapter, ["set", *argv])
            assert frames == [expected]

    def test_extend08_dehum_byte_identical(self, mock_adapter: str) -> None:
        frames = self._sent_frames(mock_adapter, ["set", "--dehum", "50"])
        assert frames == ["fc410130100804000032000000000000000000000040"]

    def test_general_plus_extend08_sends_both(self, mock_adapter: str) -> None:
        frames = self._sent_frames(
            mock_adapter, ["set", "--fan", "s3", "--power-saving", "on"]
        )
        assert frames == [
            "fc410130100108020103070300000000000000b04174",
            "fc4101301008080000000a0000000000000000000064",
        ]

    def test_nothing_to_set(self, mock_adapter: str, capsys: pytest.CaptureFixture[str]) -> None:
        assert ac_smart.main(["--ip", mock_adapter, "set"]) == 1
        assert "nothing to set" in capsys.readouterr().err

    def test_bad_temp_rejected_before_any_frame(self, mock_adapter: str) -> None:
        assert ac_smart.main(["--ip", mock_adapter, "set", "--temp", "33"]) == 1
        assert ac_smart.main(["--ip", mock_adapter, "set", "--temp", "24.25"]) == 1
        assert ac_smart.main(["--ip", mock_adapter, "set", "--temp", "15.5"]) == 1

    def test_bad_remote_lock_rejected(self, mock_adapter: str) -> None:
        assert ac_smart.main(["--ip", mock_adapter, "set", "--remote-lock", "bogus"]) == 1


# Status XML with the user's real captured CODE frames (dry, 25 C, room 23.5 C,
# all-zero energy fields) for monitor tests.
STATUS_XML = "<LSV>" + "".join(
    f"<CODE><VALUE>{c}</VALUE></CODE>" for c in TestSummarize.REAL_CODES
) + "</LSV>"


class TestMonitor:
    def _args(self, out: str, count: int = 2, interval: float = 10.0):
        return ac_smart.argparse.Namespace(
            ip="x", timeout=1.0, interval=interval, out=out, count=count, format=None
        )

    def test_csv(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ac_smart, "post_smart", lambda *a, **k: STATUS_XML)
        out = tmp_path / "log.csv"
        assert ac_smart.cmd_monitor(self._args(str(out)), sleep=lambda s: None) == 0
        lines = out.read_text().splitlines()
        assert lines[0] == (
            "timestamp,operating,power_w,energy_kwh,ac_power,mode,set_temp_c,room_temp_c"
        )
        assert len(lines) == 3
        assert lines[1].endswith(",off,dry,25,23.5")
        assert ",0,0," in lines[1]

    def test_jsonl_by_extension(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ac_smart, "post_smart", lambda *a, **k: STATUS_XML)
        out = tmp_path / "log.jsonl"
        assert ac_smart.cmd_monitor(self._args(str(out)), sleep=lambda s: None) == 0
        rows = [json.loads(line) for line in out.read_text().splitlines()]
        assert len(rows) == 2
        assert rows[0]["mode"] == "dry"
        assert rows[0]["power_w"] == 0
        assert rows[0]["room_temp_c"] == "23.5"

    def test_interval_floor(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ac_smart, "post_smart", lambda *a, **k: STATUS_XML)
        with pytest.raises(ValueError):
            ac_smart.cmd_monitor(
                self._args(str(tmp_path / "x.csv"), interval=5.0), sleep=lambda s: None
            )

    def test_poll_failure_is_skipped(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = []

        def flaky(*a: object, **k: object) -> str:
            calls.append(1)
            if len(calls) == 1:
                raise OSError("boom")
            return STATUS_XML

        monkeypatch.setattr(ac_smart, "post_smart", flaky)
        out = tmp_path / "log.csv"
        assert ac_smart.cmd_monitor(self._args(str(out), count=1), sleep=lambda s: None) == 0
        assert len(out.read_text().splitlines()) == 2  # header + one good sample
