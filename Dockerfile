# syntax=docker/dockerfile:1
# Base image pinned by digest for reproducible builds; Dependabot proposes updates.
FROM python:3.13-alpine@sha256:8bcfa47b04b853a17aaf909362b847b2112fd6ec4105ea553ef9a43bafbb5916

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

# Runtime is standard-library only; --no-deps makes that explicit in the image.
RUN pip install --no-cache-dir --no-deps . \
    && adduser -D -H appuser

USER appuser
ENTRYPOINT ["mitsubishi-echonet"]
CMD ["--help"]
