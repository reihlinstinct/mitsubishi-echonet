# syntax=docker/dockerfile:1
# Base image pinned by digest for reproducible builds; Dependabot proposes updates.
FROM python:3.15-alpine@sha256:0f9552bb28711333da16915ec146f5e6bb44a7fdac9ced15dc17c2e0206346dd

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
