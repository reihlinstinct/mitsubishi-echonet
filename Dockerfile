# syntax=docker/dockerfile:1
# Base image pinned by digest for reproducible builds; Dependabot proposes updates.
FROM python:3.15-alpine@sha256:20a82dbc2783112ddb91d1534603f802d222a3e63e593cd002eb2ac534e70211

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

# Runtime is standard-library only; --no-deps makes that explicit in the image.
RUN pip install --no-cache-dir --no-deps . \
    && adduser -D -H appuser

# /smart power control: standalone stdlib-only script plus a dispatcher that
# routes its subcommands (smart-status/on/off/set/monitor/echonet-test).
COPY scripts/ac_smart.py /app/ac_smart.py
COPY docker/entrypoint.sh /app/docker-entrypoint.sh

USER appuser
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["--help"]
