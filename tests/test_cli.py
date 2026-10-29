"""Unit tests for the CLI (network functions mocked)."""

from __future__ import annotations

import pytest

from mitsubishi_echonet import cli, echonet, smart
from mitsubishi_echonet.echonet import (
    EOJ_CONTROLLER,
    EOJ_HOME_AIR_CONDITIONER,
    ESV_GET_RES,
    EchonetFrame,
    ProbeResult,
)

MP = pytest.MonkeyPatch
Cap = pytest.CaptureFixture[str]


def _ok_result() -> smart.SmartResult:
    return smart.SmartResult(True, 200, "<ESV>x</ESV>", None)


def _fail_result() -> smart.SmartResult:
    return smart.SmartResult(False, None, "", "cannot reach")


class TestParser:
    def test_requires_command(self) -> None:
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args([])

    def test_enable_requires_ip(self) -> None:
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["enable"])

    def test_enable_parses(self) -> None:
        args = cli.build_parser().parse_args(["enable", "--ip", "192.0.2.10"])
        assert args.command == "enable"
        assert args.ip == "192.0.2.10"


class TestToggleCommands:
    def test_enable_success(self, monkeypatch: MP, capsys: Cap) -> None:
        monkeypatch.setattr(smart, "enable_echonet", lambda ip, **kw: _ok_result())
        assert cli.main(["enable", "--ip", "192.0.2.10"]) == 0
        assert "accepted the command" in capsys.readouterr().out

    def test_enable_failure(self, monkeypatch: MP, capsys: Cap) -> None:
        monkeypatch.setattr(smart, "enable_echonet", lambda ip, **kw: _fail_result())
        assert cli.main(["enable", "--ip", "192.0.2.10"]) == 1
        assert "cannot reach" in capsys.readouterr().err

    def test_disable_success(self, monkeypatch: MP, capsys: Cap) -> None:
        monkeypatch.setattr(smart, "disable_echonet", lambda ip, **kw: _ok_result())
        assert cli.main(["disable", "--ip", "192.0.2.10"]) == 0
        assert "accepted the command" in capsys.readouterr().out


def _probe() -> ProbeResult:
    frame = EchonetFrame(tid=1, seoj=EOJ_HOME_AIR_CONDITIONER, deoj=EOJ_CONTROLLER, esv=ESV_GET_RES)
    return ProbeResult(address="192.0.2.10", port=3610, frame=frame)


class TestStatus:
    def test_enabled(self, monkeypatch: MP, capsys: Cap) -> None:
        monkeypatch.setattr(echonet, "tcp_reachable", lambda *a, **kw: True)
        monkeypatch.setattr(echonet, "query", lambda *a, **kw: _probe())
        assert cli.main(["status", "--ip", "192.0.2.10"]) == 0
        assert "ENABLED" in capsys.readouterr().out

    def test_reachable_but_off(self, monkeypatch: MP, capsys: Cap) -> None:
        monkeypatch.setattr(echonet, "tcp_reachable", lambda *a, **kw: True)
        monkeypatch.setattr(echonet, "query", lambda *a, **kw: None)
        assert cli.main(["status", "--ip", "192.0.2.10"]) == 1
        assert "OFF" in capsys.readouterr().out

    def test_unreachable(self, monkeypatch: MP, capsys: Cap) -> None:
        monkeypatch.setattr(echonet, "tcp_reachable", lambda *a, **kw: False)
        monkeypatch.setattr(echonet, "query", lambda *a, **kw: None)
        assert cli.main(["status", "--ip", "192.0.2.10"]) == 1
        assert "offline or wrong IP" in capsys.readouterr().err


class TestDiscover:
    def test_found(self, monkeypatch: MP, capsys: Cap) -> None:
        monkeypatch.setattr(echonet, "discover", lambda **kw: [_probe()])
        assert cli.main(["discover"]) == 0
        assert "192.0.2.10" in capsys.readouterr().out

    def test_none_found(self, monkeypatch: MP, capsys: Cap) -> None:
        monkeypatch.setattr(echonet, "discover", lambda **kw: [])
        assert cli.main(["discover"]) == 1
        assert "No ECHONET Lite devices" in capsys.readouterr().out


class TestCapabilitiesCommand:
    def _maps(self, get_map: list[int], set_map: list[int] | None = None) -> dict:
        return {
            echonet.EPC_STATUS_CHANGE_MAP: [0x80],
            echonet.EPC_SET_PROPERTY_MAP: get_map if set_map is None else set_map,
            echonet.EPC_GET_PROPERTY_MAP: get_map,
        }

    def test_lists_properties_and_timers(self, monkeypatch: MP, capsys: Cap) -> None:
        monkeypatch.setattr(
            echonet,
            "read_property_maps",
            lambda ip, **kw: self._maps([0x80, 0x90, 0x91, 0xB0, 0xB3]),
        )
        assert cli.main(["capabilities", "--ip", "192.0.2.10"]) == 0
        out = capsys.readouterr().out
        assert "0xb0 operation mode" in out
        assert "Timer properties exposed:" in out
        assert "0x90 ON-timer reservation" in out

    def test_no_timers_reported(self, monkeypatch: MP, capsys: Cap) -> None:
        monkeypatch.setattr(
            echonet, "read_property_maps", lambda ip, **kw: self._maps([0x80, 0xB0, 0xB3])
        )
        assert cli.main(["capabilities", "--ip", "192.0.2.10"]) == 0
        assert "No ECHONET timer properties" in capsys.readouterr().out

    def test_no_reply_reachable(self, monkeypatch: MP, capsys: Cap) -> None:
        none_maps = {epc: None for epc in (0x9D, 0x9E, 0x9F)}
        monkeypatch.setattr(echonet, "read_property_maps", lambda ip, **kw: none_maps)
        monkeypatch.setattr(echonet, "tcp_reachable", lambda *a, **kw: True)
        assert cli.main(["capabilities", "--ip", "192.0.2.10"]) == 1
        assert "ECHONET Lite is OFF" in capsys.readouterr().out

    def test_no_reply_unreachable(self, monkeypatch: MP, capsys: Cap) -> None:
        none_maps = {epc: None for epc in (0x9D, 0x9E, 0x9F)}
        monkeypatch.setattr(echonet, "read_property_maps", lambda ip, **kw: none_maps)
        monkeypatch.setattr(echonet, "tcp_reachable", lambda *a, **kw: False)
        assert cli.main(["capabilities", "--ip", "192.0.2.10"]) == 1
        assert "offline or wrong IP" in capsys.readouterr().err
