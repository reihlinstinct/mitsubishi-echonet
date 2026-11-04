"""Client for the adapter's unauthenticated local ``/smart`` HTTP endpoint.

The MAC-577IF-E family exposes an HTTP server on port 80. Most routes require
HTTP basic auth, but ``/smart`` accepts encrypted XML service commands without
credentials. Commands are AES-128-CBC encrypted with a device key that is the
factory default on every adapter analyzed by the community so far (the literal
string ``unregistered`` zero-padded to 16 bytes), base64-encoded, and wrapped
in an ``<ESV>`` element. The enable/disable payloads below were produced with
that key; the enable payload is the well-known one from
https://github.com/scottyphillips/echonetlite_homeassistant/issues/226 and the
disable payload is its ``ECHONET OFF`` counterpart, matching the command
mafredri/meltools' melsmart sends for ``-disable-echonet``.

Provenance and protocol research:
https://github.com/ncaunt/meldec/issues/2
https://github.com/mafredri/meltools
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import smart_crypto

SMART_PATH = "/smart"
DEFAULT_PORT = 80
DEFAULT_TIMEOUT = 10.0

_ENABLE_B64 = (
    "7WVvmfhMYzGVi70nyFhmKEy9Jo3Hg3994vi9y1kEgDFWd/1ch9RWDUgY4HgsvMHFvP93"
    "fQ30AvEJCNcd0GTwPID0F8V5eyMVj/qAQCXFqYrRtJh8MIpm2/h7jZ2SsPj0"
)
_DISABLE_B64 = (
    "7WVvmfhMYzGVi70nyFhmKEy9Jo3Hg3994vi9y1kEgDFWd/1ch9RWDUgY4HgsvMHFrMnv"
    "UyB5uOhckVDUeRqtaYVBoGRCoB5MtcGSsQRkYZRk+cUWdicHToe7pj8/OKss"
)

ENABLE_ECHONET_PAYLOAD = f'<?xml version="1.0" encoding="UTF-8"?><ESV>{_ENABLE_B64}</ESV>'
DISABLE_ECHONET_PAYLOAD = f'<?xml version="1.0" encoding="UTF-8"?><ESV>{_DISABLE_B64}</ESV>'

ADAPTER_KEY = b"unregistered".ljust(16, b"\0")
"""Factory-default device key (provenance in the module docstring)."""

CSV_STATUS_QUERY = "<CSV></CSV>"
"""Read-only status query; the decrypted reply carries an <ECHONET> flag."""

_ECHONET_FLAG_RE = re.compile(r"<ECHONET>\s*([^<]*?)\s*</ECHONET>")
_ESV_BODY_RE = re.compile(r"<ESV>\s*([^<]+?)\s*</ESV>")


@dataclass(frozen=True)
class SmartResult:
    """Outcome of a POST to the /smart endpoint."""

    ok: bool
    status: int | None
    body: str
    error: str | None


def smart_url(ip: str, port: int = DEFAULT_PORT) -> str:
    """Build the /smart URL for an adapter."""
    if port == DEFAULT_PORT:
        return f"http://{ip}{SMART_PATH}"
    return f"http://{ip}:{port}{SMART_PATH}"


def post_smart(
    ip: str,
    payload: str,
    *,
    port: int = DEFAULT_PORT,
    timeout: float = DEFAULT_TIMEOUT,
) -> SmartResult:
    """POST an ESV payload to the adapter's /smart endpoint.

    No credentials are sent: /smart is the one unauthenticated route. A
    successful enable/disable replies 200 with an encrypted <ESV> body.
    """
    request = urllib.request.Request(
        smart_url(ip, port),
        data=payload.encode("utf-8"),
        headers={"Content-Type": "text/xml"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status: int | None = response.status
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace") if exc.fp else ""
        return SmartResult(False, exc.code, body, f"HTTP {exc.code}: {exc.reason}")
    except urllib.error.URLError as exc:
        return SmartResult(False, None, "", f"cannot reach {smart_url(ip, port)}: {exc.reason}")
    except TimeoutError:
        url = smart_url(ip, port)
        return SmartResult(False, None, "", f"timed out after {timeout}s reaching {url}")

    if status is not None and 200 <= status < 300 and "<ESV>" in body:
        return SmartResult(True, status, body, None)
    if status is not None and 200 <= status < 300:
        return SmartResult(False, status, body, "unexpected response body (no <ESV> element)")
    return SmartResult(False, status, body, f"HTTP status {status}")


def enable_echonet(
    ip: str, *, port: int = DEFAULT_PORT, timeout: float = DEFAULT_TIMEOUT
) -> SmartResult:
    """Enable ECHONET Lite on the adapter (factory-default key devices)."""
    return post_smart(ip, ENABLE_ECHONET_PAYLOAD, port=port, timeout=timeout)


def disable_echonet(
    ip: str, *, port: int = DEFAULT_PORT, timeout: float = DEFAULT_TIMEOUT
) -> SmartResult:
    """Disable ECHONET Lite on the adapter (factory-default key devices)."""
    return post_smart(ip, DISABLE_ECHONET_PAYLOAD, port=port, timeout=timeout)


def query_echonet_flag(
    ip: str, *, port: int = DEFAULT_PORT, timeout: float = DEFAULT_TIMEOUT
) -> str | None:
    """HTTP-reported ECHONET flag ("ON"/"OFF") from the /smart status reply.

    Read-only: POSTs the CSV status query and reads the ``<ECHONET>`` element
    of the decrypted reply. Returns None when the flag cannot be read
    (unreachable adapter, unexpected reply, or a non-default device key).
    Callers must treat None as "unknown", never as "off" (issue #70).
    """
    payload = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<ESV>{smart_crypto.encrypt_xml(CSV_STATUS_QUERY, ADAPTER_KEY)}</ESV>"
    )
    result = post_smart(ip, payload, port=port, timeout=timeout)
    if not result.ok:
        return None
    body = _ESV_BODY_RE.search(result.body)
    if body is None:
        return None
    try:
        plain = smart_crypto.decrypt_xml(body.group(1), ADAPTER_KEY)
    except ValueError:
        return None
    # The decrypted reply can carry trailing padding bytes, so extract the
    # flag with a regex instead of parsing the document (issue #70).
    flag = _ECHONET_FLAG_RE.search(plain)
    return flag.group(1) if flag is not None else None
