# syntax=docker/dockerfile:1
#
# Multi-stage, hash-verified, non-root image for the video pipeline.
# Hardening follows NIST SP 800-190 and OWASP Docker guidance; see SECURITY.md.
#   builder      : resolves pinned, hash-verified runtime wheels into /opt/venv (wheels only, no compilers)
#   builder-dev  : adds pinned dev/QA/security tooling on top
#   runtime      : slim image with FFmpeg + runtime venv, non-root (render, research)
#   dev          : runtime + dev tooling + git (tests, lint, security scans)
#
# The base image is pinned by multi-arch index digest (amd64 + arm64); Dependabot updates it.

FROM python:3.14-slim-bookworm@sha256:48b13b003dda20b16f9442b8475aa05fe21bf6579a8c881db92ffb4d8fd20f83 AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

# ---------------------------------------------------------------------------
FROM base AS builder
COPY requirements.lock /tmp/requirements.lock
# --only-binary keeps compilers out of every stage; --require-hashes enforces the lock (SSDF PW.4).
RUN python -m venv /opt/venv \
 && pip install --require-hashes --only-binary=:all: --no-deps -r /tmp/requirements.lock

# ---------------------------------------------------------------------------
FROM builder AS builder-dev
COPY requirements-dev.lock /tmp/requirements-dev.lock
RUN pip install --require-hashes --no-deps -r /tmp/requirements-dev.lock

# ---------------------------------------------------------------------------
FROM base AS runtime
ARG APP_UID=1000
ARG APP_GID=1000
# ffmpeg: encoding/muxing primitive. libgl1/libegl1/libfontconfig1: shared libs skia-python links against.
# tzdata: IANA zones so upload scheduling is DST-correct (zoneinfo).
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libgl1 libegl1 libfontconfig1 tzdata \
 && rm -rf /var/lib/apt/lists/* /var/cache/apt/* \
 && groupadd --gid "${APP_GID}" app \
 && useradd --uid "${APP_UID}" --gid "${APP_GID}" --no-create-home --shell /usr/sbin/nologin app
COPY --from=builder /opt/venv /opt/venv
ENV HOME=/tmp/home \
    XDG_CACHE_HOME=/tmp/cache \
    MPLCONFIGDIR=/tmp/cache \
    PYTHONPATH=/app
WORKDIR /app
USER app:app
CMD ["python", "-m", "src.production.produce", "--help"]

# ---------------------------------------------------------------------------
FROM runtime AS dev
USER root
# git: detect-secrets and pre-commit enumerate tracked files through it (read-only use).
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/* /var/cache/apt/* \
 && git config --system --add safe.directory /app
COPY --from=builder-dev /opt/venv /opt/venv
ENV MYPY_CACHE_DIR=/tmp/cache/mypy \
    RUFF_CACHE_DIR=/tmp/cache/ruff \
    COVERAGE_FILE=/tmp/cache/.coverage
USER app:app
CMD ["bash"]
