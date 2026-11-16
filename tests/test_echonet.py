"""Unit tests for ECHONET Lite framing and probing (no real network)."""

from __future__ import annotations

import socket

import pytest

from mitsubishi_echonet import echonet
from mitsubishi_echonet.echonet import (
    EOJ_CONTROLLER,
    EOJ_HOME_AIR_CONDITIONER,
    EPC_OPERATION_STATUS,
    ESV_GET,
    ESV_GET_RES,
    ESV_GET_SNA,
    ESV_INF,
    EchonetFrame,
    EchonetProperty,
    FrameError,
    ProbeResult,
)


class TestFrameRoundTrip:
    def test_empty_properties(self) -> None:
        frame = EchonetFrame(
            tid=0x1234, seoj=EOJ_CONTROLLER, deoj=EOJ_HOME_AIR_CONDITIONER, esv=ESV_GET
        )
        parsed = EchonetFrame.from_bytes(frame.to_bytes())
        assert parsed == frame

    def test_with_properties(self) -> None:
        frame = EchonetFrame(
            tid=1,
            seoj=EOJ_HOME_AIR_CONDITIONER,
            deoj=EOJ_CONTROLLER,
            esv=ESV_GET_RES,
            properties=(
                EchonetProperty(EPC_OPERATION_STATUS, b"\x30"),
                EchonetProperty(0xB3, b"\x18"),
            ),
        )
        parsed = EchonetFrame.from_bytes(frame.to_bytes())
        assert parsed == frame
        assert parsed.properties[0].edt == b"\x30"

    def test_header_layout(self) -> None:
        data = EchonetFrame(
            tid=0x002A,
            seoj=EOJ_CONTROLLER,
            deoj=EOJ_HOME_AIR_CONDITIONER,
            esv=ESV_GET,
            properties=(EchonetProperty(EPC_OPERATION_STATUS),),
        ).to_bytes()
        assert data[0:2] == bytes([0x10, 0x81])
        assert data[2:4] == b"\x00\x2a"
        assert data[4:7] == b"\x05\xff\x01"
        assert data[7:10] == b"\x01\x30\x01"
        assert data[10] == ESV_GET
        assert data[11] == 1  # OPC
        assert data[12] == EPC_OPERATION_STATUS
        assert data[13] == 0  # PDC


class TestFrameParsing:
    def test_rejects_short_frame(self) -> None:
        with pytest.raises(FrameError, match="too short"):
            EchonetFrame.from_bytes(b"\x10\x81\x00")

    def test_rejects_bad_header(self) -> None:
        good = EchonetFrame(tid=1, seoj=1, deoj=1, esv=ESV_GET).to_bytes()
        with pytest.raises(FrameError, match="bad ECHONET header"):
            EchonetFrame.from_bytes(bytes([0x11]) + good[1:])

    def test_rejects_truncated_property(self) -> None:
        # OPC says 1 property; the property header claims 5 EDT bytes but only
        # one byte is present.
        raw = bytes.fromhex("1081000105ff0101300172018005") + b"\x30"
        with pytest.raises(FrameError, match="truncated"):
            EchonetFrame.from_bytes(raw)

    def test_rejects_truncated_property_header(self) -> None:
        # OPC says 1 property but the frame ends right after OPC.
        raw = bytes.fromhex("1081000105ff010130017201")
        with pytest.raises(FrameError, match="truncated property header"):
            EchonetFrame.from_bytes(raw)

    def test_rejects_out_of_range_values(self) -> None:
        with pytest.raises(FrameError):
            EchonetFrame(tid=0x1_0000, seoj=1, deoj=1, esv=ESV_GET)
        with pytest.raises(FrameError):
            EchonetFrame(tid=1, seoj=0x1_000000, deoj=1, esv=ESV_GET)
        with pytest.raises(FrameError):
            EchonetProperty(epc=0x100)
        with pytest.raises(FrameError):
            EchonetProperty(epc=0x80, edt=b"x" * 256)


class TestBuilders:
    def test_get_request_targets_home_ac_operation_status(self) -> None:
        frame = EchonetFrame.from_bytes(echonet.build_get_request(tid=7))
        assert frame.tid == 7
        assert frame.seoj == EOJ_CONTROLLER
        assert frame.deoj == EOJ_HOME_AIR_CONDITIONER
        assert frame.esv == ESV_GET
        assert frame.properties == (EchonetProperty(EPC_OPERATION_STATUS),)

    def test_describe(self) -> None:
        frame = EchonetFrame(
            tid=1,
            seoj=EOJ_HOME_AIR_CONDITIONER,
            deoj=EOJ_CONTROLLER,
            esv=ESV_GET_RES,
            properties=(EchonetProperty(EPC_OPERATION_STATUS, b"\x30"),),
        )
        text = echonet.describe(frame)
        assert "Get_Res" in text
        assert "home air conditioner" in text
        assert "operation status=30" in text


class FakeSocket:
    """Minimal UDP-socket stand-in with a scripted reply queue."""

    def __init__(self, replies: list[tuple[bytes, tuple[str, int]]]) -> None:
        self.replies = list(replies)
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.timeout: float | None = None

    def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
        self.sent.append((data, addr))

    def settimeout(self, value: float) -> None:
        self.timeout = value

    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        if not self.replies:
            raise TimeoutError
        return self.replies.pop(0)

    def close(self) -> None:
        pass


def _reply_frame() -> bytes:
    return EchonetFrame(
        tid=1,
        seoj=EOJ_HOME_AIR_CONDITIONER,
        deoj=EOJ_CONTROLLER,
        esv=ESV_GET_RES,
        properties=(EchonetProperty(EPC_OPERATION_STATUS, b"\x30"),),
    ).to_bytes()


class TestQuery:
    def test_reply_means_enabled(self) -> None:
        sock = FakeSocket([(_reply_frame(), ("192.0.2.10", 3610))])
        result = echonet.query("192.0.2.10", timeout=0.1, sock=sock)  # type: ignore[arg-type]
        assert result is not None
        assert result.address == "192.0.2.10"
        assert result.frame.esv == ESV_GET_RES
        (sent, addr), = sock.sent
        assert addr == ("192.0.2.10", echonet.ECHONET_PORT)
        assert EchonetFrame.from_bytes(sent).esv == ESV_GET

    def test_silence_returns_none(self) -> None:
        sock = FakeSocket([])
        assert echonet.query("192.0.2.10", timeout=0.05, sock=sock) is None  # type: ignore[arg-type]

    def test_unparseable_datagrams_are_skipped(self) -> None:
        sock = FakeSocket(
            [(b"garbage!!", ("192.0.2.99", 3610)), (_reply_frame(), ("192.0.2.10", 3610))]
        )
        result = echonet.query("192.0.2.10", timeout=0.1, sock=sock)  # type: ignore[arg-type]
        assert result is not None
        assert result.address == "192.0.2.10"


class TestQueryMatching:
    def _frame(self, tid: int, esv: int, epc: int) -> bytes:
        return EchonetFrame(
            tid=tid,
            seoj=EOJ_HOME_AIR_CONDITIONER,
            deoj=EOJ_CONTROLLER,
            esv=esv,
            properties=(EchonetProperty(epc, b"\x30"),),
        ).to_bytes()

    def test_wrong_tid_is_ignored(self) -> None:
        sock = FakeSocket(
            [
                (self._frame(2, ESV_GET_RES, EPC_OPERATION_STATUS), ("192.0.2.10", 3610)),
                (self._frame(1, ESV_GET_RES, EPC_OPERATION_STATUS), ("192.0.2.10", 3610)),
            ]
        )
        result = echonet.query("192.0.2.10", timeout=0.1, sock=sock)  # type: ignore[arg-type]
        assert result is not None
        assert result.frame.tid == 1

    def test_wrong_epc_is_ignored(self) -> None:
        sock = FakeSocket(
            [
                (self._frame(1, ESV_GET_RES, 0xB3), ("192.0.2.10", 3610)),
                (self._frame(1, ESV_GET_RES, EPC_OPERATION_STATUS), ("192.0.2.10", 3610)),
            ]
        )
        result = echonet.query("192.0.2.10", timeout=0.1, sock=sock)  # type: ignore[arg-type]
        assert result is not None
        assert result.frame.properties[0].epc == EPC_OPERATION_STATUS

    def test_inf_broadcast_is_ignored(self) -> None:
        sock = FakeSocket([(self._frame(1, ESV_INF, EPC_OPERATION_STATUS), ("192.0.2.10", 3610))])
        assert echonet.query("192.0.2.10", timeout=0.05, sock=sock) is None  # type: ignore[arg-type]

    def test_get_sna_counts_as_reply(self) -> None:
        sock = FakeSocket(
            [(self._frame(1, ESV_GET_SNA, EPC_OPERATION_STATUS), ("192.0.2.10", 3610))]
        )
        result = echonet.query("192.0.2.10", timeout=0.1, sock=sock)  # type: ignore[arg-type]
        assert result is not None
        assert result.frame.esv == ESV_GET_SNA


class TestDiscover:
    def test_collects_multiple_responders(self) -> None:
        sock = FakeSocket(
            [
                (_reply_frame(), ("192.0.2.10", 3610)),
                (_reply_frame(), ("192.0.2.11", 3610)),
            ]
        )
        results = echonet.discover(timeout=0.1, sock=sock)  # type: ignore[arg-type]
        assert {r.address for r in results} == {"192.0.2.10", "192.0.2.11"}
        (sent, addr), = sock.sent
        assert addr == (echonet.MULTICAST_GROUP, echonet.ECHONET_PORT)

    def test_empty(self) -> None:
        assert echonet.discover(timeout=0.05, sock=FakeSocket([])) == []  # type: ignore[arg-type]


class TestTcpReachable:
    def test_refused(self) -> None:
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()  # port now has no listener
        assert echonet.tcp_reachable("127.0.0.1", port, timeout=0.2) is False

    def test_accepts(self) -> None:
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        try:
            assert echonet.tcp_reachable("127.0.0.1", port, timeout=1.0) is True
        finally:
            server.close()


class TestParsePropertyMap:
    def test_empty(self) -> None:
        assert echonet.parse_property_map(b"") == []

    def test_short_list(self) -> None:
        edt = bytes([3, 0x80, 0xB3, 0xBB])
        assert echonet.parse_property_map(edt) == [0x80, 0xB3, 0xBB]

    def test_short_list_truncated(self) -> None:
        # Count claims 5 but only 2 EPC bytes present: keep what is there.
        assert echonet.parse_property_map(bytes([5, 0x80, 0xB0])) == [0x80, 0xB0]

    def test_bitmap(self) -> None:
        # 16+ properties: 16-byte bitmap, bit j (LSB first) of byte i is
        # EPC 0x80 + 0x10*j + i. Set 0x80 (byte 0 bit 0), 0xB3 (byte 3 bit 3),
        # and 0x9F (byte 15 bit 1).
        bitmap = bytearray(16)
        bitmap[0] = 0x01
        bitmap[3] = 0x08
        bitmap[15] = 0x02
        edt = bytes([16]) + bytes(bitmap)
        assert echonet.parse_property_map(edt) == [0x80, 0xB3, 0x9F]

    def test_bitmap_full(self) -> None:
        edt = bytes([16]) + b"\xff" * 16
        epcs = echonet.parse_property_map(edt)
        assert len(epcs) == 128
        assert set(epcs) == set(range(0x80, 0x100))


class TestReadPropertyMaps:
    def _map_reply(self, epc: int, edt: bytes) -> ProbeResult:
        frame = EchonetFrame(
            tid=1,
            seoj=EOJ_HOME_AIR_CONDITIONER,
            deoj=EOJ_CONTROLLER,
            esv=ESV_GET_RES,
            properties=(echonet.EchonetProperty(epc, edt),),
        )
        return ProbeResult(address="192.0.2.10", port=3610, frame=frame)

    def test_decodes_each_map(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_query(ip: str, *, tid: int, deoj: int, epc: int, timeout: float):
            assert ip == "192.0.2.10"
            return self._map_reply(epc, bytes([2, 0x80, 0xB0]))

        monkeypatch.setattr(echonet, "query", fake_query)
        maps = echonet.read_property_maps("192.0.2.10")
        assert maps == {
            echonet.EPC_STATUS_CHANGE_MAP: [0x80, 0xB0],
            echonet.EPC_SET_PROPERTY_MAP: [0x80, 0xB0],
            echonet.EPC_GET_PROPERTY_MAP: [0x80, 0xB0],
        }

    def test_no_reply_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(echonet, "query", lambda *a, **kw: None)
        maps = echonet.read_property_maps("192.0.2.10")
        assert all(value is None for value in maps.values())

    def test_sna_reply_stays_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_query(ip: str, **kw):
            frame = EchonetFrame(
                tid=1,
                seoj=EOJ_HOME_AIR_CONDITIONER,
                deoj=EOJ_CONTROLLER,
                esv=ESV_GET_SNA,
                properties=(echonet.EchonetProperty(kw["epc"]),),
            )
            return ProbeResult(address="192.0.2.10", port=3610, frame=frame)

        monkeypatch.setattr(echonet, "query", fake_query)
        maps = echonet.read_property_maps("192.0.2.10")
        assert all(value is None for value in maps.values())

    def test_empty_map_edt_stays_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_query(ip: str, **kw):
            return self._map_reply(kw["epc"], b"")

        monkeypatch.setattr(echonet, "query", fake_query)
        maps = echonet.read_property_maps("192.0.2.10")
        assert all(value is None for value in maps.values())

    def test_reply_without_map_epc_stays_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_query(ip: str, **kw):
            return self._map_reply(0x80, b"\x30")

        monkeypatch.setattr(echonet, "query", fake_query)
        maps = echonet.read_property_maps("192.0.2.10")
        assert all(value is None for value in maps.values())
