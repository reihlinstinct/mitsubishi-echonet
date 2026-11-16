"""Wiki language parity: languages come only from the wiki page files.

A wiki guide is ``<lang>-<slug>.md`` in ``docs/wiki/``. The set of languages is
discovered from those file names, and each page opens with one metadata
comment, its language block: code, own name, text direction, status, and for a
translation the page it was made from. Nothing else lists the languages: adding
a language is adding its pages (plus their entries in Home and _Sidebar, which
the index test below checks, with the README source links). Stdlib only, offline.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIKI = ROOT / "docs" / "wiki"
CODE_DIRS = ("src", "scripts", "tools", "tests")

PAGE_RE = re.compile(r"^([a-z]{2,3})-([a-z0-9]+(?:-[a-z0-9]+)*)\.md$")
META_RE = re.compile(r"\A<!-- wiki-lang: (.+?) -->\n")
META_KEYS = {"code", "name", "dir", "status"}
DIRS = {"ltr", "rtl"}
STATUSES = {"source", "machine-draft", "reviewed"}
FENCE_RE = re.compile(r"^```[^\n]*\n(.*?)^```[ \t]*$", re.S | re.M)
HEADING_RE = re.compile(r"^(#{1,6}) +(\d*)", re.M)
URL_RE = re.compile(r"https?://[^\s)>\]]+")
WIKI_LINK_RE = re.compile(r"\]\(([A-Za-z0-9_-]+)\)")
README_SOURCE_RE = re.compile(r"\]\(docs/wiki/([a-z0-9-]+)\.md\)")


def pages() -> dict[str, dict[str, Path]]:
    """language code -> slug -> page path, discovered from the file names."""
    out: dict[str, dict[str, Path]] = {}
    for path in sorted(WIKI.glob("*.md")):
        m = PAGE_RE.match(path.name)
        if m:
            out.setdefault(m.group(1), {})[m.group(2)] = path
    return out


def languages() -> list[str]:
    return sorted(pages())


def meta(path: Path) -> dict[str, str]:
    m = META_RE.match(path.read_text(encoding="utf-8"))
    assert m, f"{path.name}: first line must be a '<!-- wiki-lang: ... -->' block"
    out: dict[str, str] = {}
    for field in m.group(1).split(";"):
        key, sep, value = field.partition("=")
        assert sep, f"{path.name}: malformed field {field!r}"
        out[key.strip()] = value.strip()
    return out


def prose(text: str) -> str:
    return FENCE_RE.sub("", text)


def code_blocks(text: str) -> list[list[str]]:
    """Fenced blocks with comments and printed messages removed: what is left
    (commands, flags, addresses, payloads) must match the source exactly."""
    blocks = []
    for body in FENCE_RE.findall(text):
        lines = []
        for line in body.splitlines():
            if "print(" in line or line.lstrip().startswith("#"):
                continue
            line = re.sub(r"\s+#\s.*$", "", line).rstrip()
            if line:
                lines.append(line)
        blocks.append(lines)
    return blocks


def wiki_links(text: str) -> set[str]:
    return set(WIKI_LINK_RE.findall(prose(text)))


def hardcoded_codes(text: str, codes: set[str]) -> list[str]:
    """Quoted literals that name a language code or a language-prefixed page."""
    alt = "|".join(sorted(map(re.escape, codes)))
    return re.findall(rf"[\"']((?:{alt})(?:-[a-z0-9-]+)?)[\"']", text)


def test_languages_are_discovered_from_page_files():
    found = pages()
    assert found, "no <lang>-<slug>.md pages found in docs/wiki"
    for path in WIKI.glob("*.md"):
        assert PAGE_RE.match(path.name) or path.name in {"Home.md", "_Sidebar.md", "_Footer.md"}, (
            f"{path.name}: wiki guides must be named <lang>-<slug>.md"
        )


def test_every_language_has_every_page():
    found = pages()
    slugs = set().union(*(set(p) for p in found.values()))
    missing = sorted(f"{code}-{s}.md" for code in found for s in slugs - set(found[code]))
    assert not missing, f"pages missing in some languages: {missing}"


def test_each_page_has_one_language_block():
    for code, by_slug in pages().items():
        for slug, path in by_slug.items():
            m = meta(path)
            keys = set(m) - {"source"}
            assert keys == META_KEYS, f"{path.name}: block keys {sorted(m)}"
            assert m["code"] == code, f"{path.name}: block code {m['code']!r}"
            assert m["name"], f"{path.name}: empty language name"
            assert m["dir"] in DIRS, f"{path.name}: dir {m['dir']!r}"
            assert m["status"] in STATUSES, f"{path.name}: status {m['status']!r}"
            text = path.read_text(encoding="utf-8")
            assert text.count("<!-- wiki-lang:") == 1, f"{path.name}: one block only"
            if m["status"] == "source":
                assert "source" not in m, f"{path.name}: a source page names no source"
            else:
                src = m.get("source", "")
                sm = PAGE_RE.match(src + ".md")
                assert sm and sm.group(2) == slug, f"{path.name}: source {src!r}"
                assert (WIKI / f"{src}.md").is_file(), f"{path.name}: source {src!r} missing"
                assert meta(WIKI / f"{src}.md")["status"] == "source"


def test_language_block_is_the_same_on_every_page_of_a_language():
    for code, by_slug in pages().items():
        names = {(meta(p)["name"], meta(p)["dir"]) for p in by_slug.values()}
        assert len(names) == 1, f"{code}: differing name/dir across pages: {names}"


def test_every_slug_has_exactly_one_source_page():
    slugs: Counter[str] = Counter()
    for by_slug in pages().values():
        for slug, path in by_slug.items():
            if meta(path)["status"] == "source":
                slugs[slug] += 1
    every = set().union(*(set(p) for p in pages().values()))
    assert {s: slugs[s] for s in every} == dict.fromkeys(every, 1)


def test_translations_keep_the_structure_of_their_source():
    for by_slug in pages().values():
        for path in by_slug.values():
            m = meta(path)
            if m["status"] == "source":
                continue
            text = path.read_text(encoding="utf-8")
            src = (WIKI / f"{m['source']}.md").read_text(encoding="utf-8")
            name = path.name
            assert HEADING_RE.findall(prose(text)) == HEADING_RE.findall(prose(src)), (
                f"{name}: headings (levels and section numbers) differ from {m['source']}"
            )
            assert code_blocks(text) == code_blocks(src), (
                f"{name}: commands or payloads in code blocks differ from {m['source']}"
            )
            assert set(URL_RE.findall(text)) == set(URL_RE.findall(src)), (
                f"{name}: links differ from {m['source']}"
            )


def test_machine_drafts_link_to_their_source():
    for by_slug in pages().values():
        for path in by_slug.values():
            m = meta(path)
            if m["status"] == "machine-draft":
                text = path.read_text(encoding="utf-8")
                assert m["source"] in wiki_links(text), f"{path.name}: no link to {m['source']}"


def test_home_and_sidebar_list_exactly_the_guides():
    guides = {p.stem for by_slug in pages().values() for p in by_slug.values()}
    home = wiki_links((WIKI / "Home.md").read_text(encoding="utf-8"))
    sidebar = wiki_links((WIKI / "_Sidebar.md").read_text(encoding="utf-8"))
    assert home == guides, f"Home.md wiki links {sorted(home)} != guides {sorted(guides)}"
    assert sidebar == guides | {"Home"}, f"_Sidebar.md wiki links {sorted(sidebar)}"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    sources = {m.group(1) for m in README_SOURCE_RE.finditer(readme)}
    assert sources == guides, f"README.md guide sources {sorted(sources)} != {sorted(guides)}"


def test_every_wiki_link_resolves():
    existing = {p.stem for p in WIKI.glob("*.md")}
    for path in WIKI.glob("*.md"):
        dead = wiki_links(path.read_text(encoding="utf-8")) - existing
        assert not dead, f"{path.name}: links to missing pages {sorted(dead)}"


def test_no_language_code_is_hardcoded_in_code():
    codes = set(languages())
    hits = []
    for top in CODE_DIRS:
        for path in sorted((ROOT / top).rglob("*.py")):
            for literal in hardcoded_codes(path.read_text(encoding="utf-8"), codes):
                hits.append(f"{path.relative_to(ROOT)}: {literal!r}")
    assert not hits, f"language codes hardcoded outside docs/wiki: {hits}"


def test_scan_catches_a_hardcoded_code():
    sample = "LANG = 'zz'\nPAGE = \"zz-some-guide\"\nOTHER = 'zzz'\n"
    assert hardcoded_codes(sample, {"zz"}) == ["zz", "zz-some-guide"]


def test_code_blocks_ignore_comments_and_messages_only():
    a = "```bash\nrun --ip 1.2.3.4   # one\n# two\n```\n```py\nprint('x')\ny = 1\n```\n"
    b = "```bash\nrun --ip 1.2.3.4   # un\n# deux\n```\n```py\nprint('autre')\ny = 1\n```\n"
    assert code_blocks(a) == code_blocks(b)
    assert code_blocks(a) != code_blocks(b.replace("1.2.3.4", "1.2.3.5"))
