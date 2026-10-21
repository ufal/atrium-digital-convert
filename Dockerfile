# syntax=docker/dockerfile:1.7
FROM python:3.11-slim AS base

ARG ATRIUM_RUNNER_IMAGE=""
ARG ATRIUM_RUNNER_REPO="https://github.com/ufal/atrium-digital-convert"
ARG ATRIUM_RUNNER_REF=""

ENV ATRIUM_RUNNER_IMAGE=${ATRIUM_RUNNER_IMAGE} \
    ATRIUM_RUNNER_REPO=${ATRIUM_RUNNER_REPO} \
    ATRIUM_RUNNER_REF=${ATRIUM_RUNNER_REF} \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ── Distro security patches, applied at build time ───────────────────────────
# `python:3.11-slim` is a floating TAG, and nothing in this ecosystem bumps it:
# no repo declares a `docker` dependabot ecosystem (docker_gha_roadmap.md, H6),
# so the base layer is whatever Docker Hub last rebuilt. On 2026-09-13 that layer
# carried perl-base 5.40.1-6 with three FIXABLE CRITICAL CVEs — CVE-2026-13221,
# CVE-2026-42496 and CVE-2026-8376, all fixed in 5.40.1-6+deb13u1. The release
# gate in atrium-project's docker-tool.reusable.yml ("Fail the release on fixable
# CRITICAL vulnerabilities") blocks on exactly that class, and because the
# promotion step is `if: success()`, a blocked release publishes by DIGEST ONLY —
# the `:<version>` and `:latest` tags are never applied.
#
# It has already cost two releases: translator v1.0.0-beta (2026-09-13, both
# targets) and nlp-enrich v0.20.2 (2026-09-15, run 34970419474, all three
# targets). THIS repo had not been tagged since, which is the only reason it had
# not happened here too — the gate is `if: startsWith(github.ref, 'refs/tags/')`,
# so day-to-day `test` pushes never surface it. (atrium-project#53)
#
# `upgrade` rather than `install --only-upgrade perl-base`, deliberately. The gate
# blocks on *fixable* CRITICALs — precisely those the distro already ships a patch
# for — so the fix that matches the gate's own definition is "apply the distro's
# available patches", not a package name that has to be edited by hand the next
# time a different one is announced.
#
# CACHE INTERACTION, which is what makes this hold rather than run once: the build
# uses `cache-from: type=gha`, so an apt layer high in the file would be served
# from cache forever and silently stop patching. It sits HERE, immediately after
# the ENV block that embeds ATRIUM_RUNNER_REF, because CI passes that as
# `github.ref_name` — a value unique to each release tag. The ENV layer therefore
# changes on every release, busting this layer with it, so every released image is
# scanned against a freshly patched base while day-to-day `test` pushes still hit
# the cache. Do not move this above the ENV block.
#
# One apt layer, not two: the upgrade and the install share a single `apt-get
# update`, so the package lists are fetched once and removed once.
# Guarded by tests/test_dockerfile_security_layer.py (atrium-project#53).
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Base deps (pydantic/requests/jsonschema/lxml) — see requirements.txt. The converter's
# readers come in the `digital` stage, the web server in `api`.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

# Non-root runtime user. Owned atrium:0 and group-writable (`g=u`): the arbitrary-UID
# convention (OpenShift's), atrium-project#69 / roadmap B6. docker-compose.yaml runs these
# images as `user: "${ATRIUM_UID:-10001}:0"`, so on Linux the container can run as the uid
# that owns the ./data bind mount, and a uid with no passwd entry still reaches /app,
# /data and $HOME through group 0. HOME is explicit because without a passwd entry
# it would be `/`. The default runtime -- uid 10001 as the owner -- is unchanged. Every
# stage below re-applies the same ownership to what it adds.
RUN useradd --create-home --uid 10001 atrium \
    && mkdir -p /data \
    && chown -R atrium:0 /app /data /home/atrium \
    && chmod -R g=u /app /data /home/atrium
ENV HOME=/home/atrium

USER atrium


# ---------------------------------------------------------------------------
# Born-digital converter, command line — published as :<version>-digital
#
# api_util/digital_to_json.py turns a born-digital document directly into an
# atrium_document record. It is the ORIGINATOR of a born-digital record's positional
# plane: with an AMČR seed (--document-json) it keeps the seed's identity and checks its
# source.sha512 against the file; without one it creates the record.
#
# The image carries the LIGHT engine only — requirements_digital.txt: pdfplumber,
# pypdfium2, python-docx, jsonschema, lxml, all permissive, no models, no network — plus
# headless LibreOffice for the legacy DOC/XLS (atrium-digital-convert#4, accepted by AMČR
# 2026-09-26: MPL-2.0, a conditional component in para_config.txt, it only converts the
# file). `-core` alone cannot load a document ("source file could not be loaded"): the
# writer and calc filters are what convert DOC and XLS, so both are installed, in their
# `-nogui` builds. The opt-in heavy engine is its own stage below.
# ---------------------------------------------------------------------------
FROM base AS digital

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-writer-nogui \
        libreoffice-calc-nogui \
    && rm -rf /var/lib/apt/lists/*
COPY requirements_digital.txt ./
RUN pip install -r requirements_digital.txt
RUN chown -R atrium:0 /app /home/atrium \
    && chmod -R g=u /app /home/atrium
ENV LIBREOFFICE_BIN=soffice
USER atrium

ENTRYPOINT ["python", "api_util/digital_to_json.py"]
CMD ["--help"]


# ---------------------------------------------------------------------------
# Digital-born converter, heavy engine — built on demand, NOT published
#
#   docker build --target digital-docling -t atrium-digital-convert-digital-docling .
#   docker run --rm -v "$PWD:/data" atrium-digital-convert-digital-docling \
#       /data/report.pdf --engine docling --document-json-out /data/report.document.json
#
# `--engine docling` (api_util/digital_docling.py): Docling's layout and table models
# decide reading order, headings, furniture and tables on complex PDFs; the light
# engine's lines keep their geometry. Not in docker.yml's build-targets on purpose: a
# torch image of several GB on every push, for an opt-in engine.
#
# The model weights are downloaded HERE, at build time — the build needs the Hugging
# Face Hub, the running container does not — into /opt/docling-models, which
# DOCLING_ARTIFACTS_PATH names. TableFormer's weights are CDLA-Permissive-2.0: see
# requirements_digital_docling.txt for what that does to provenance.license.
#
# Declared before `api` so that an untargeted `docker build` still builds the last
# stage, `api`.
# ---------------------------------------------------------------------------
FROM digital AS digital-docling

USER root
COPY requirements_digital_docling.txt ./
RUN pip install -r requirements_digital_docling.txt \
    && docling-tools models download layout tableformer -o /opt/docling-models \
    && chown -R atrium:0 /opt/docling-models /home/atrium \
    && chmod -R g=u /opt/docling-models /home/atrium
ENV DOCLING_ARTIFACTS_PATH=/opt/docling-models
USER atrium

ENTRYPOINT ["python", "api_util/digital_to_json.py"]
CMD ["--help"]


# ---------------------------------------------------------------------------
# API service — published as :<version>-api, THE production image (atrium-project#72)
# `api-digital` (atrium-digital-convert#2): POST /reformat (the AMČR route's endpoint;
# calls no other service) and POST /describe (the per-page assessment; calls
# page-classification and ocr-postprocess only when PAGE_CLASSIFICATION_URL /
# OCR_POSTPROCESS_URL are set). The `digital` stack + the web server; no model, no GPU.
# ---------------------------------------------------------------------------
FROM digital AS api

USER root
COPY service/requirements.txt ./service/requirements.txt
RUN pip install -r service/requirements.txt
RUN chown -R atrium:0 /app /home/atrium \
    && chmod -R g=u /app /home/atrium
USER atrium

# EXPOSE tracks the DEFAULT port: it is image metadata and cannot read $PORT at
# runtime. Set PORT to move the listener, and publish with `-p <port>:<port>` to
# match. (issue #58)
EXPOSE 8000

# STOPSIGNAL is the default (SIGTERM) — declared explicitly so a future edit cannot
# change it silently; service/api.py's lifespan chains to uvicorn's own handler for it
# via serve_lifecycle (service/atrium_service.py, issue #55).
STOPSIGNAL SIGTERM

# PORT and HOST are read by service/api.py's __main__ block; PORT is also the port
# service/healthcheck.py probes, which is why setting it used to make the container
# permanently unhealthy — the probe moved and the listener did not. Declared here so
# `docker inspect` is self-documenting and so the probe still has a value if the code
# default ever drifts. (issue #58)
#
# GRACEFUL_SHUTDOWN_S carries the `--timeout-graceful-shutdown 20` that used to sit on
# the ENTRYPOINT line. It bounds uvicorn's wait for in-flight HTTP requests. A
# conversion takes seconds; a /describe with stages configured waits for them (up to
# STAGE_TIMEOUT_S per call), so raise this together with the deployment's grace period
# when /describe is used (docs/k8s_deployment.md in the hub).
ENV PORT=8000 GRACEFUL_SHUTDOWN_S=20

# `python -m service.api`, NOT `python service/api.py`: a script launch puts
# sys.path[0] at /app/service with no package context, so `from .atrium_service import ...`
# raises "attempted relative import with no known parent package" before the app is
# built. `-m` keeps sys.path[0] at /app — byte for byte the environment the old
# `uvicorn service.api:app` entrypoint ran in, so every repo-root import still
# resolves. (issue #58)
ENTRYPOINT ["python", "-m", "service.api"]
CMD []
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "/app/service/healthcheck.py"]