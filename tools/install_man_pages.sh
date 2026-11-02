#!/bin/sh
# Install the shipped mitsubishi-echonet man pages (issue #54).
# Usage: tools/install_man_pages.sh [PREFIX]   (default: /usr/local)
# Installs to $DESTDIR$PREFIX/share/man/man1/ (DESTDIR for staged packaging).
# Only the shipped console script has pages; internal CI/dev scripts
# (scripts/ac_smart.py, scripts/ci, scripts/security, docker entrypoint)
# deliberately get none.
set -eu
PREFIX=${1:-/usr/local}
SRC="$(cd "$(dirname "$0")/.." && pwd)/man/man1"
DEST="${DESTDIR:-}$PREFIX/share/man/man1"
mkdir -p "$DEST"
for page in "$SRC"/mitsubishi-echonet*.1; do
    install -m 644 "$page" "$DEST/"
    echo "installed $DEST/$(basename "$page")"
done
