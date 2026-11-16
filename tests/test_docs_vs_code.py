"""Two-way doc-vs-code check for the public CLI surface.

Every subcommand and flag the CLI (console script, scripts/ac_smart.py and the
container entrypoint) exposes must appear in the docs, and every subcommand or
flag the docs show as CLI syntax must exist in the code. The parser is
introspected, never executed; docs are scanned as text. Stdlib only, offline.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from mitsubishi_echonet.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]
DOC_FILES = [ROOT / "README.md"] + sorted((ROOT / "docs").rglob("*.md"))
ALLOWLIST = ROOT / ".github" / "docs-vs-code-allowlist.txt"
AC_SMART = ROOT / "scripts" / "ac_smart.py"
ENTRYPOINT = ROOT / "docker" / "entrypoint.sh"

MARKERS = ("mitsubishi-echonet", "ac_smart.py")
SPAN_RE = re.compile(r"```[^\n]*\n(.*?)```|`([^`\n]+)`", re.S)
WORD_RE = re.compile(r"^[a-z][a-z-]*$")
FLAG_RE = re.compile(r"--[a-z][a-z0-9-]*")


def _allowlist() -> dict[str, str]:
    out: dict[str, str] = {}
    if ALLOWLIST.is_file():
        for line in ALLOWLIST.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            token, _, reason = line.partition("#")
            token = token.strip()
            assert reason.strip(), f"allowlist entry without a reason: {line}"
            out[token] = reason.strip()
    return out


def cli_surface() -> tuple[set[str], set[str]]:
    """(subcommands, flags) of the installed console script, introspected."""
    parser = build_parser()
    commands: set[str] = set()
    flags: set[str] = set()

    def walk(p: argparse.ArgumentParser) -> None:
        for action in p._actions:
            flags.update(o for o in action.option_strings if o.startswith("--"))
            if isinstance(action, argparse._SubParsersAction):
                commands.update(action.choices)
                for sub in action.choices.values():
                    walk(sub)

    walk(parser)
    flags.discard("--help")
    return commands, flags


def script_surface() -> tuple[set[str], set[str]]:
    """(subcommands, flags) of scripts/ac_smart.py, regex-extracted."""
    text = AC_SMART.read_text(encoding="utf-8")
    commands = set(re.findall(r'add_parser\(\s*["\']([a-z-]+)["\']', text))
    for names in re.findall(r"for \w+ in \(([^)]+)\):\s*\n\s*\w*\(sub\.add_parser\(\w+\)\)", text):
        commands.update(re.findall(r'["\']([a-z-]+)["\']', names))
    flags = set(re.findall(r'add_argument\(\s*["\'](--[a-z-]+)["\']', text))
    return commands, flags


def entrypoint_aliases() -> set[str]:
    """Subcommand tokens the container entrypoint routes (docker/entrypoint.sh)."""
    if not ENTRYPOINT.is_file():
        return set()
    text = ENTRYPOINT.read_text(encoding="utf-8")
    out: set[str] = set()
    for m in re.finditer(r"^\s*([a-z][a-z-]*(?:\s*\|\s*[a-z][a-z-]*)*)\)", text, re.M):
        out.update(a.strip() for a in m.group(1).split("|"))
    return out


def code_tokens() -> tuple[set[str], set[str]]:
    commands, flags = cli_surface()
    scommands, sflags = script_surface()
    return commands | scommands | entrypoint_aliases(), flags | sflags


def doc_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in DOC_FILES)


def doc_spans(text: str):
    for m in SPAN_RE.finditer(text):
        yield m.group(1) if m.group(1) is not None else m.group(2)


def line_after(span: str, marker: str):
    """Same-line remainder after each marker occurrence (not `marker.git` etc.)."""
    for m in re.finditer(re.escape(marker) + r"(?![\w.-])", span):
        yield span[m.end():].split("\n", 1)[0]


def first_word_after(span: str, marker: str, commands: set[str]) -> str | None:
    """First bare lowercase word after `marker` on its line, skipping flags.

    A flag consumes the next token as its value only when that token is not
    itself a flag and not a known subcommand, so a boolean flag placed before
    the subcommand cannot swallow it.
    """
    for rest in line_after(span, marker):
        tokens = rest.split()
        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token.startswith("--"):
                if i + 1 < len(tokens) and not tokens[i + 1].startswith("--") \
                        and tokens[i + 1].casefold() not in commands:
                    i += 2
                else:
                    i += 1
                continue
            if WORD_RE.match(token):
                return token
            i += 1
    return None


def doc_flags(text: str) -> set[str]:
    """Flags the docs present as CLI syntax: backticked `--flag` anywhere, plus
    any `--flag` on a CLI command line (fenced or inline) after the invocation."""
    out = set(re.findall(r"`(--[a-z][a-z0-9-]*)`", text))
    for span in doc_spans(text):
        for marker in MARKERS:
            for rest in line_after(span, marker):
                out.update(FLAG_RE.findall(rest))
    return out


def test_every_cli_token_is_documented():
    commands, flags = code_tokens()
    text = doc_text().casefold()
    allowed = _allowlist()
    missing = sorted(
        t for t in (commands | flags) if t.casefold() not in text and t not in allowed
    )
    assert not missing, f"CLI tokens missing from docs: {missing}"


def test_every_documented_token_exists():
    commands, flags = code_tokens()
    known_commands = {t.casefold() for t in commands}
    known_flags = {t.casefold() for t in flags}
    allowed = {k.casefold() for k in _allowlist()}
    stale: set[str] = set()
    for doc in DOC_FILES:
        text = doc.read_text(encoding="utf-8")
        for token in doc_flags(text):
            if token.casefold() not in known_flags | allowed:
                stale.add(f"{doc.relative_to(ROOT)}: flag {token}")
        for span in doc_spans(text):
            for marker in MARKERS:
                token = first_word_after(span, marker, known_commands)
                if token and token.casefold() not in known_commands | allowed:
                    stale.add(f"{doc.relative_to(ROOT)}: subcommand {token}")
    assert not stale, f"documented CLI tokens missing from code: {sorted(stale)}"


def test_doc_flags_catches_fake_flag_in_fence():
    fence = "```bash\nmitsubishi-echonet status --ip 192.168.1.50 --totally-fake-flag\n```\n"
    assert "--totally-fake-flag" in doc_flags(fence)
    assert "--ip" in doc_flags(fence)


def test_doc_flags_catches_fake_flag_inline():
    assert "--totally-fake-flag" in doc_flags("`ac_smart.py status --totally-fake-flag`")


def test_doc_flags_ignores_wrapper_flags_before_the_invocation():
    fence = (
        "```bash\n"
        "docker run --rm --network host mitsubishi-echonet status --ip 192.168.1.50\n"
        "```\n"
    )
    found = doc_flags(fence)
    assert "--ip" in found
    assert "--rm" not in found and "--network" not in found


def test_first_word_after_boolean_flag_keeps_subcommand():
    commands, _ = code_tokens()
    span = "python3 scripts/ac_smart.py --buzzer status"
    assert first_word_after(span, "ac_smart.py", {c.casefold() for c in commands}) == "status"


def test_first_word_after_value_flag_skips_the_value():
    commands, _ = code_tokens()
    span = "python3 scripts/ac_smart.py --ip 192.168.1.50 status"
    assert first_word_after(span, "ac_smart.py", {c.casefold() for c in commands}) == "status"
