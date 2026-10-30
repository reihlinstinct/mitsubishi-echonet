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

SPAN_RE = re.compile(r"```[^\n]*\n(.*?)```|`([^`\n]+)`", re.S)


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
    commands = set(re.findall(r'add_parser\(["\']([a-z-]+)["\']', text))
    for names in re.findall(r"for \w+ in \(([^)]+)\):\s*\n\s*\w*\(sub\.add_parser\(\w+\)\)", text):
        commands.update(re.findall(r'["\']([a-z-]+)["\']', names))
    flags = set(re.findall(r'add_argument\(["\'](--[a-z-]+)["\']', text))
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


def first_word_after(span: str, marker: str) -> str | None:
    """First bare lowercase word after `marker`, skipping --flag value pairs."""
    m0 = re.search(re.escape(marker) + r"(?![\w.-])", span)
    if not m0:
        return None
    rest = span[m0.end():].split("\n", 1)[0]
    rest = re.sub(r"--[a-z-]+(?:\s+\S+)?", " ", rest)
    m = re.search(r"(?<![\w-])([a-z][a-z-]*)(?![\w-])", rest)
    return m.group(1) if m else None


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
    known = {t.casefold() for t in commands | flags} | {k.casefold() for k in _allowlist()}
    stale: set[str] = set()
    for doc in DOC_FILES:
        text = doc.read_text(encoding="utf-8")
        for token in re.findall(r"`(--[a-z][a-z0-9-]*)`", text):
            if token.casefold() not in known:
                stale.add(f"{doc.relative_to(ROOT)}: flag {token}")
        for span in doc_spans(text):
            for marker in ("mitsubishi-echonet", "ac_smart.py"):
                token = first_word_after(span, marker)
                if token and token.casefold() not in known:
                    stale.add(f"{doc.relative_to(ROOT)}: subcommand {token}")
    assert not stale, f"documented CLI tokens missing from code: {sorted(stale)}"
