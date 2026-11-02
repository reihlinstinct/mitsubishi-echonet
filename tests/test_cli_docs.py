"""Man pages and --help for the shipped console script (issue #54).

- --help smoke test for the console entrypoint and every subcommand.
- Spec-vs-parser: subcommand set and per-subcommand flags match cli_spec.
- Drift test: committed man/man1/* pages match tools/build_man_pages.py output.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import build_man_pages  # noqa: E402

from mitsubishi_echonet import cli_spec  # noqa: E402
from mitsubishi_echonet.cli import build_parser  # noqa: E402


def run_help(*args: str) -> str:
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, 'src'); from mitsubishi_echonet.cli import run; run()",
            *args,
            "--help",
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return " ".join(proc.stdout.split())  # argparse wraps help text


def test_help_smoke_top_level():
    out = run_help()
    assert " ".join(cli_spec.PROGRAM_DESCRIPTION.split()) in out
    for name, cmd in cli_spec.COMMANDS.items():
        assert name in out
        assert " ".join(cmd.summary.split()) in out


def test_help_smoke_every_subcommand():
    for name, cmd in cli_spec.COMMANDS.items():
        out = run_help(name)
        for opt in cmd.options:
            assert opt.flag in out
            assert " ".join(opt.help.split()) in out


def test_parser_matches_spec():
    parser = build_parser()
    subs = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    assert set(subs.choices) == set(cli_spec.COMMANDS)
    for name, cmd in cli_spec.COMMANDS.items():
        sub_parser = subs.choices[name]
        flags = {o for a in sub_parser._actions for o in a.option_strings if o.startswith("--")} - {
            "--help"
        }
        assert flags == {o.flag for o in cmd.options}, name


def test_man_pages_up_to_date():
    assert build_man_pages.main(["--check"]) == 0


def test_man_pages_cover_exactly_the_shipped_commands():
    committed = {p.name for p in (ROOT / "man" / "man1").glob("*.1")}
    expected = {f"{cli_spec.PROGRAM}.1"} | {
        f"{cli_spec.PROGRAM}-{c.name}.1" for c in cli_spec.COMMANDS.values()
    }
    assert committed == expected
