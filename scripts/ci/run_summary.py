#!/usr/bin/env python3
"""Append a run summary to GITHUB_STEP_SUMMARY.

Stdlib-only and deterministic. Sections:
1. Jobs: result and duration per job, totals, skipped jobs with their
   if-conditions (durations via the public jobs API when reachable).
2. What changed: commits and changed files for push / pull_request runs.
3. Coverage: changed files mapped to the checks that cover them via
   .github/ci-coverage-map.json; files matching no mapping are listed as
   changed without declared coverage.

Everything is best-effort: a missing piece of context degrades one section,
never the job. This step never fails the workflow.
"""
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

MAP_PATH = ".github/ci-coverage-map.json"


def sh(*args):
    return subprocess.run(list(args), capture_output=True, text=True,
                          check=True).stdout


def fmt_dur(seconds):
    seconds = round(seconds)
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m {seconds % 60:02d}s"


def glob_to_regex(pattern):
    """Translate a gitignore-style glob to a regex. `**` crosses directories,
    `*` and `?` stay within one path segment."""
    out = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if pattern[i:i + 2] == "**":
                i += 2
                if pattern[i:i + 1] == "/":
                    i += 1
                    out.append("(?:.*/)?")
                else:
                    out.append(".*")
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = pattern.find("]", i)
            out.append(pattern[i:j + 1] if j != -1 else "\\[")
            i = j + 1 if j != -1 else i + 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def job_durations(env):
    """job name -> seconds, from the public jobs API. Empty on any failure."""
    token = env.get("GITHUB_TOKEN", "")
    repo = env.get("GITHUB_REPOSITORY", "")
    run_id = env.get("GITHUB_RUN_ID", "")
    api = env.get("GITHUB_API_URL", "")
    if not repo or not run_id or not api:
        return {}
    url = f"{api}/repos/{repo}/actions/runs/{run_id}/jobs?per_page=100"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return {}
    durations = {}
    for job in data.get("jobs", []):
        started, completed = job.get("started_at"), job.get("completed_at")
        if not started or not completed:
            continue
        try:
            t0 = datetime.datetime.fromisoformat(started)
            t1 = datetime.datetime.fromisoformat(completed)
        except ValueError:
            continue
        durations[job.get("name", "")] = (t1 - t0).total_seconds()
    return durations


def _yaml_plain_scalar(text):
    """Strip a trailing comment from an unquoted YAML scalar: in a plain
    scalar, ` #` starts a comment (name: X (issue #14) parses as X
    (issue). Quoted scalars are returned unchanged."""
    if text.startswith(("'", '"')):
        return text
    return text.split(" #", 1)[0].rstrip()


def load_job_meta(env):
    """(job id -> `if:` expression, job id -> static display name), parsed
    from the workflow file. Job-level keys sit at 4-space indent; `name:`
    values containing expressions are skipped (not statically knowable)."""
    ref = env.get("GITHUB_WORKFLOW_REF", "")
    path = ref.split("@")[0].split("/", 2)[-1] if "@" in ref else ""
    if not path or not os.path.exists(path):
        return {}, {}
    conditions = {}
    names = {}
    current = None
    try:
        with open(path, encoding="utf-8") as fh:
            in_jobs = False
            for line in fh:
                if re.match(r"^jobs:\s*$", line):
                    in_jobs = True
                    continue
                if in_jobs:
                    m = re.match(r"^  ([A-Za-z0-9_.-]+):\s*$", line)
                    if m:
                        current = m.group(1)
                        continue
                    if line and not line.startswith(" "):
                        in_jobs = False
                        current = None
                        continue
                    m = re.match(r"^    if:\s*(.+?)\s*$", line)
                    if m and current:
                        conditions[current] = m.group(1)
                        continue
                    m = re.match(r"^    name:\s*(.+?)\s*$", line)
                    if m and current and "${{" not in m.group(1):
                        names[current] = _yaml_plain_scalar(m.group(1))
    except OSError:
        return {}, {}
    return conditions, names


def duration_for(job_id, durations, display_names):
    """Seconds for one needs-context job id, matched against API display
    names: exact id, then a static workflow `name:`, then matrix children
    ("<id> (<value>)"). None when nothing matches."""
    candidates = [job_id]
    disp = display_names.get(job_id)
    if disp and disp != job_id:
        candidates.append(disp)
    total = 0.0
    found = False
    for cand in candidates:
        if cand in durations:
            total += durations[cand]
            found = True
        else:
            children = [v for n, v in durations.items()
                        if n.startswith(cand + " (")]
            if children:
                total += sum(children)
                found = True
    return total if found else None


def md_escape(text):
    """Render untrusted text (commit subjects, file paths, if-conditions)
    as literal text in the step summary: control characters, newlines
    and the Unicode line/paragraph separators (U+2028/U+2029, which
    str.splitlines also splits on) become spaces, HTML metacharacters
    become entities (the summary renders raw HTML), and the Markdown
    metacharacters that could break out of a table cell, code span or
    link are backslash-escaped."""
    text = "".join(
        ch if (ch >= " " and ch not in "\x7f\x85\u2028\u2029") else " "
        for ch in text)
    text = (text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))
    return (text.replace("\\", "\\\\")
            .replace("`", "\\`")
            .replace("|", "\\|")
            .replace("[", "\\[")
            .replace("]", "\\]"))


def _self_test():
    """Regression checks for the summary tooling itself. Runs on every
    invocation; a failure means the tool is defective, so the job stops."""
    assert md_escape("a`b|c\nd[e]f\\g") == "a\\`b\\|c d\\[e\\]f\\\\g"
    assert md_escape("<b>&") == "&lt;b&gt;&amp;"
    assert md_escape("<a href='x'>y</a>") == "&lt;a href='x'&gt;y&lt;/a&gt;"
    assert md_escape("a\u2028b\u2029c") == "a b c"
    # the forged-row scenario: U+2028 in a subject must not split the
    # log line, and any remaining fragment stays escaped literal text
    forged = "deadbee real \u2028` | [x](/forged-link) | forged"
    assert forged.split("\n") == [forged]
    short, _, subject = forged.partition(" ")
    rendered = f"- `{short}` {md_escape(subject)}"
    assert rendered.count("\n") == 0 and "\u2028" not in rendered 
    assert md_escape("\x00\x1f\r\n") == "    "
    assert md_escape("plain text") == "plain text"
    dur = {"test (3.11)": 60.0, "test (3.12)": 70.0, "lint": 30.0,
           "Build the thing": 200.0}
    assert duration_for("test", dur, {}) == 130.0
    assert duration_for("build", dur, {"build": "Build the thing"}) == 200.0
    assert duration_for("lint", dur, {}) == 30.0
    assert duration_for("missing", dur, {}) is None
    assert glob_to_regex("scripts/*.ts").match("scripts/x.ts")
    assert not glob_to_regex("scripts/*.ts").match("scripts/ci/x.py")
    assert glob_to_regex("a/**").match("a/b/c")
    assert _yaml_plain_scalar("Language shootout (issue #14)") == \
        "Language shootout (issue"
    assert _yaml_plain_scalar('"a # b"') == '"a # b"'



def load_event(env):
    path = env.get("GITHUB_EVENT_PATH", "")
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def changed_ranges(env, event):
    """(log range, diff range) for this run, or None.

    On pull_request, GITHUB_SHA is the synthetic merge commit, so it
    would add a "Merge ..." entry to the commit list: list commits
    base..head and diff files base...head (merge base) instead. On push,
    before..after serves both."""
    name = env.get("GITHUB_EVENT_NAME", "")
    sha = env.get("GITHUB_SHA", "")
    if name == "pull_request":
        pr = event.get("pull_request") or {}
        base = pr.get("base", {}).get("sha", "")
        head = pr.get("head", {}).get("sha", "")
        if base and head:
            return f"{base}..{head}", f"{base}...{head}"
        return None
    if name == "push":
        before = event.get("before", "")
        if before and set(before) != {"0"} and sha:
            return f"{before}..{sha}", f"{before}..{sha}"
    return None


def load_map():
    try:
        with open(MAP_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    mappings = []
    for entry in data.get("mappings", []):
        pattern = entry.get("pattern", "")
        if pattern:
            mappings.append((glob_to_regex(pattern), pattern,
                             entry.get("checks", [])))
    return mappings


STATUS_WORD = {"A": "added", "M": "modified", "D": "deleted",
               "R": "renamed", "C": "copied", "T": "type changed"}


def main():
    try:
        _self_test()
    except AssertionError:
        sys.exit("run_summary.py self-test failed; the summary tool is "
                 "defective, not writing a summary")
    env = os.environ
    out = []
    repo = env.get("GITHUB_REPOSITORY", "")
    server = env.get("GITHUB_SERVER_URL", "")
    sha = env.get("GITHUB_SHA", "")
    workflow = env.get("GITHUB_WORKFLOW", "")

    out.append(f"## Run summary - {workflow}\n")
    out.append(f"- Event: `{env.get('GITHUB_EVENT_NAME', '')}`")
    if repo and sha and server:
        out.append(f"- Commit: [`{sha[:7]}`]({server}/{repo}/commit/{sha})")
    elif sha:
        out.append(f"- Commit: `{sha[:7]}`")

    # --- Jobs -------------------------------------------------------------
    needs = {}
    try:
        needs = json.loads(env.get("NEEDS_JSON", "{}"))
    except json.JSONDecodeError:
        pass
    durations = job_durations(env)
    conditions, display_names = load_job_meta(env)
    if needs:
        out.append("\n### Jobs\n")
        out.append("| Job | Result | Duration |")
        out.append("| --- | --- | --- |")
        counts = {}
        total = 0.0
        for job in sorted(needs):
            result = needs[job].get("result", "unknown")
            counts[result] = counts.get(result, 0) + 1
            dur = duration_for(job, durations, display_names)
            if dur is not None:
                total += dur
            out.append(f"| `{job}` | {result} | "
                       f"{fmt_dur(dur) if dur is not None else 'n/a'} |")
        out.append("")
        out.append("Totals: "
                   + ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
                   + ".")
        if total:
            out.append(f"Summed job time: {fmt_dur(total)} (each job is "
                       "billed rounded up to a whole minute; minutes on "
                       "public repos are free).")
        if not durations:
            out.append("Per-job durations unavailable (jobs API not "
                       "reachable with this token).")
        skipped = [j for j in sorted(needs)
                   if needs[j].get("result") == "skipped"]
        if skipped:
            out.append("\nSkipped jobs:")
            for job in skipped:
                cond = conditions.get(job)
                out.append(f"- `{job}`" +
                           (f" - runs only if {md_escape(cond)}"
                            if cond else
                            " - its condition was not met"))
    else:
        out.append("\nNo upstream jobs declared in `needs`.")

    # --- What changed -----------------------------------------------------
    event = load_event(env)
    ranges = changed_ranges(env, event)
    files = []
    if ranges:
        log_rng, diff_rng = ranges
        out.append(f"\n### What changed in this run (`{diff_rng}`)\n")
        try:
            log = sh("git", "log", "--format=%h %s", log_rng).strip()
            out.append("Commits:")
            # split("\n"), not splitlines(): a subject containing
            # U+2028/U+2029 must not smuggle a forged extra row.
            for line in log.split("\n") or ["(none)"]:
                short, _, subject = line.partition(" ")
                out.append(f"- `{short}` {md_escape(subject)}")
            # -z: NUL-delimited, no quoting of non-ASCII paths.
            diff = sh("git", "diff", "--name-status", "-z", diff_rng)
            records = [r for r in diff.split("\x00") if r]
            i = 0
            while i < len(records):
                status = records[i]
                if status.startswith(("R", "C")):
                    i += 1  # skip old path; report the new one
                if i + 1 > len(records) - 1:
                    break
                files.append((status[0], records[i + 1]))
                i += 2
        except subprocess.CalledProcessError:
            out.append("Diff unavailable in this checkout "
                       "(shallow history or missing base).")
    else:
        out.append("\n### What changed in this run\n")
        out.append("Not a push or pull_request run, so there is no change "
                   "range to diff.")

    # --- Coverage ---------------------------------------------------------
    if files:
        mappings = load_map()
        out.append("\n### Coverage of the changes\n")
        if not mappings:
            out.append("Changed files (no coverage map in this repo; add "
                       f"`{MAP_PATH}` to declare which checks cover what):")
            for status, path in files:
                word = STATUS_WORD.get(status, status)
                out.append(f"- {md_escape(path)} ({word})")
        else:
            out.append("| File | Change | Checks covering it |")
            out.append("| --- | --- | --- |")
            uncovered = []
            for status, path in files:
                checks = []
                for regex, _pattern, mapped in mappings:
                    if regex.match(path):
                        checks.extend(c for c in mapped if c not in checks)
                word = STATUS_WORD.get(status, status)
                if checks:
                    out.append(f"| {md_escape(path)} | {word} | "
                               + ", ".join(f"`{c}`" for c in checks) + " |")
                else:
                    out.append(f"| {md_escape(path)} | {word} | - |")
                    uncovered.append(path)
            if uncovered:
                out.append("\nChanged without declared coverage (no "
                           f"check in `{MAP_PATH}` maps to these files):")
                for path in uncovered:
                    out.append(f"- {md_escape(path)}")
            else:
                out.append("\nEvery changed file matches at least one "
                           "coverage mapping.")

    text = "\n".join(out) + "\n"
    summary = env.get("GITHUB_STEP_SUMMARY", "")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - deliberate fail-safe: a
        # summary must never fail the workflow, whatever broke.
        sys.stderr.write(f"run_summary: degraded: {exc}\n")
