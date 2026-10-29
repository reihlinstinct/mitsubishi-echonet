# Contributing

Thanks for helping improve mitsubishi-echonet. Everything in this repo is public - treat it that way.

## Ground rules

- **No personal data, ever.** Do not paste names, phone numbers, ID numbers,
  or other identifying details into issues, PRs, code, or data files.
- **No secrets.** Credentials and tokens live in GitHub Actions secrets.
  Never print them; logs use fixed categories, not raw output.
- **Public sources only.** Data comes from public endpoints; no scraping
  behind logins, no CAPTCHA bypasses. If a source starts blocking, stop and
  open an issue instead of working around it.

## How work flows

1. **Open an issue first** (or comment on an existing one). Bugs need
   evidence (run links, logs, data samples).
2. **Small, focused PRs** referencing the issue (`fixes #N` / `refs #N`).
3. **Green CI required.** Run the test suite locally before pushing.
4. **Squash merge** once checks pass. Commit messages explain the why and
   reference the issue.

## Style

- Code and documentation are written in English unless the repo says
  otherwise; user-facing text may be Hebrew where the audience is Hebrew.
- Every bug fix lands with a regression test.
