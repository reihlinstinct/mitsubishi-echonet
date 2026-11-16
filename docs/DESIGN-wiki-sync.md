# GitHub Wiki sync design: Hebrew user guides from `docs/wiki/`

Status: **design proposal - not implemented yet.** This doc records the
decision to use each public repo's GitHub Wiki for Hebrew end-user guides and
how-tos, with the wiki content kept in git (`docs/wiki/`) and published by a
small GitHub Actions sync workflow. Written 2026-10-29.

## Decision (approved 2026-10-29)

- Canonical documentation stays in `docs/`, changed only by PR + review, as
  today.
- The GitHub Wiki holds Hebrew user guides and "how do I" pages - end-user
  material, not design or protocol docs.
- Wiki pages are source-controlled too: they live in `docs/wiki/` in the repo,
  go through the same PR + review flow as everything else, and a sync workflow
  publishes them to the repo's wiki (`<repo>.wiki.git`) on every merge to
  `main` that touches `docs/wiki/`.
- Public repos only (see "Free plan limits"). Free resources only.
- No repository settings changes beyond enabling the Wiki feature where it is
  off.

## Why keep the wiki source in the repo

- GitHub wikis are editable directly in the UI by anyone with push access, and
  those edits bypass review. With `docs/wiki/` as the source of truth, every
  page is PR-reviewed and the wiki becomes a read-only view.
- The sync workflow mirrors the folder and overwrites the wiki on each run, so
  direct UI edits do not survive the next sync. That is intentional; this
  design treats UI edits as lost work and says so on the wiki home page.

## Layout

```
docs/wiki/
  Home.md            -> wiki home page
  _Sidebar.md        -> optional sidebar shown on every wiki page
  <Page-Name>.md     -> wiki page titled "Page-Name"
```

Page filenames use ASCII slugs with hyphens (Hebrew titles go inside the page,
not in the URL), e.g. `he-ac-local-control.md`. The README keeps linking the
canonical docs in `docs/` and gains one link to the wiki as the user-guide
entry point.

## Sync workflow

`.github/workflows/wiki-sync.yml`, triggered on push to `main` with
`paths: ["docs/wiki/**", ".github/workflows/wiki-sync.yml"]` plus
`workflow_dispatch` (so the first sync can be run and inspected manually):

1. Check out the repo.
2. Clone `https://github.com/<owner>/<repo>.wiki.git`. If the clone fails, the
   wiki backend does not exist yet: GitHub only creates it after the Wiki
   feature is enabled and one first page is saved in the UI. That is a
   one-time manual step per repo; the workflow stops with instructions until
   it is done, and takes over from there.
3. Mirror `docs/wiki/` into the wiki clone (`rsync -a --delete`, Markdown
   files only, including `_Sidebar.md` / `_Footer.md`).
4. Commit and push only when there is a diff; commit message records the
   source SHA.

Concurrency group `wiki-sync` with `cancel-in-progress: true`. Workflow-level
permissions stay `contents: read`; only the sync job declares
`contents: write` (least privilege).

## Authentication and token scope

The default `GITHUB_TOKEN` with job-level `contents: write` can push to the
*same* repo's `.wiki.git` - no new secret and nothing to rotate. That is the
setup the implementation uses.

Some accounts report `403 Permission to <repo>.wiki.git denied to
github-actions[bot]` even with `contents: write`. If that happens here, the
fallback is a fine-grained personal access token scoped to that single
repository with `Contents: Read and write`, stored as a repo secret named
`WIKI_PUSH_TOKEN`, and the workflow prefers it when the secret exists.

Creating any PAT, or broadening any existing token's scope, is a credentials
change: it requires explicit owner approval beforehand and is not something
the implementation PRs do on their own.

## Bootstrap state

Rows are updated as each pilot lands. Checked 2026-10-29:

| repo | wiki enabled | wiki initialized |
| --- | --- | --- |
| mitsubishi-echonet | yes | yes (pilot done) |
| parking-reporter-app | no | no |
| parking-reporter-ios | yes | no |
| parking-report-helper | yes | no |
| parking-report-intake | yes | no |

"Enabled" is the repo setting; "initialized" means the `.wiki.git` backend
exists (it does not, on any candidate repo - no pages have ever been created).
Enabling the feature (Settings -> Wiki) and saving one first page is the
only settings/UI change and happens once per repo; the sync workflow handles
everything from there.

## Free plan limits

Wikis, like Pages, require a **public** repository on the free plan, and
Actions minutes are free for public repos. Private repos are therefore out
of scope and get no wiki work. This also
matches the standing rule that public content carries no private data: every
`docs/wiki/` page is public on the web.

## Rollout

Each step is its own issue + PR:

1. **Pilot: mitsubishi-echonet.** Add the `docs/wiki/` seed pages (split from
   the existing `docs/guide-he.md`), add the sync workflow, enable the wiki
   setting, run the first sync manually via `workflow_dispatch`, verify the
   pages render on the wiki tab.
2. `parking-reporter-app`, `parking-reporter-ios`: Hebrew user guides for
   reporting a parking violation.
3. `parking-report-helper`, `parking-report-intake`: Hebrew how-tos for the
   operator flow.

A repo only gets the workflow after its wiki setting is enabled.

## Open questions

- Does `docs/guide-he.md` move into `docs/wiki/` in the pilot (leaving a link
  behind), or stay where it is with the wiki duplicating it? Proposal: move
  it, so there is exactly one canonical Hebrew guide and the wiki is its
  rendered view.
- Sidebar structure once a repo has more than a handful of pages.
