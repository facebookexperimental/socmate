# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
#
# socmate -- AI-orchestrated ASIC pipeline
#
# This image bundles the full open-source EDA toolchain (Yosys, OpenROAD,
# Magic, netgen, KLayout, the Sky130 PDK) with the socmate Python
# orchestrator and the Claude Code CLI, so the pipeline can run
# end-to-end inside a single container -- ideal for RunPod, EC2 or any
# CI environment that doesn't have Nix.
#
# Build:
#   docker build -t socmate:latest .
#
# Run (interactive shell):
#   docker run --rm -it \
#     -e ANTHROPIC_API_KEY=sk-ant-... \
#     -v $(pwd)/.socmate:/socmate/.socmate \
#     -v $(pwd)/rtl:/socmate/rtl \
#     socmate:latest
#
# Run (headless pipeline):
#   docker run --rm \
#     -e ANTHROPIC_API_KEY=sk-ant-... \
#     -e SOCMATE_MODE=pipeline \
#     -v $(pwd)/.socmate:/socmate/.socmate \
#     socmate:latest

# -----------------------------------------------------------------------------
# Base: efabless/openlane:1.0.0 ships Yosys, OpenROAD, Magic, netgen,
# KLayout and the Sky130 PDK on a Debian/Ubuntu userland (~5 GB compressed,
# ~15 GB extracted). The Debian base matters: the npm-published Claude CLI
# is now a Bun-compiled native ELF that expects a standard FHS dynamic
# linker at /lib64/ld-linux-x86-64.so.2, which Nix-built images don't ship.
# We previously tried `ghcr.io/efabless/openlane2:2.3.10` (Nix, ~1.5 GB)
# and ended up with brittle patchelf / glibc-symlink hacks; the Debian
# base just runs claude.exe directly without any of that.
#
# Disk impact: this base no longer fits a default 32 GB Codespace
# (`docker build` hits ~25 GB cap). It does fit GHA `ubuntu-latest` after
# the disk-cleanup step in publish-image.yml, RunPod, and any normal
# laptop. Codespaces users should pull the published image instead of
# building from source, or use a larger Codespace machine.
# -----------------------------------------------------------------------------
FROM efabless/openlane:1.0.0 AS socmate

ARG DEBIAN_FRONTEND=noninteractive
ARG NODE_MAJOR=20

# OpenLane's image runs as a non-root UID 1000; switch to root for
# package installs and the layered changes below.
USER root

# System packages -- single layer to keep the apt cache tight.
#
# - ca-certificates curl git gnupg make sudo: build essentials
# - openssh-server: lets the entrypoint stand up sshd when PUBLIC_KEY is
#   set (RunPod web terminal + ssh from anywhere)
# - python3.11 + venv: the orchestrator pins 3.11; the openlane base ships
#   only python3.6 so we install fresh from apt
# - verilator: lint + sim driver for the RTL phase
# - nodejs: just for npm so we can install the Claude CLI; Node 20 LTS
#   from NodeSource (the Debian-shipped Node is too old for the CLI)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git gnupg make sudo \
        openssh-server \
        python3.11 python3.11-venv python3-pip \
        verilator \
    && curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

# Capture the resolved Claude CLI path at build time and bake it as
# CLAUDE_CLI_PATH so runtime resolution can't drift if PATH changes
# under us. Also fail the build loud if `claude --version` fails -- the
# runtime "PermissionError: ''" failure mode is much harder to debug
# than a build-time missing-binary error.
RUN set -eux \
 && CLAUDE_BIN="$(command -v claude)" \
 && test -x "${CLAUDE_BIN}" \
 && claude --version \
 && printf 'CLAUDE_CLI_PATH=%s\n' "${CLAUDE_BIN}" > /etc/socmate.env

# sshd setup so RunPod / interactive users can ssh in (and the web
# terminal works because PID 1 stays alive even after the pipeline
# exits -- see runpod_entrypoint.sh's pipeline keep-alive). Host keys
# are baked into the image; per-deploy authorized_keys is written by
# the entrypoint from the PUBLIC_KEY env var.
RUN mkdir -p /var/run/sshd /run/sshd /root/.ssh \
 && chmod 700 /root/.ssh \
 && ssh-keygen -A \
 && sed -i \
        -e 's|^#\?\s*PermitRootLogin.*|PermitRootLogin prohibit-password|' \
        -e 's|^#\?\s*PasswordAuthentication.*|PasswordAuthentication no|' \
        -e 's|^#\?\s*PubkeyAuthentication.*|PubkeyAuthentication yes|' \
        -e 's|^#\?\s*UsePAM.*|UsePAM no|' \
        /etc/ssh/sshd_config

# -----------------------------------------------------------------------------
# Python venv + socmate deps. Done in two layers so a code-only edit
# doesn't bust the dependency cache.
# -----------------------------------------------------------------------------
ENV VIRTUAL_ENV=/opt/socmate-venv
RUN python3.11 -m venv "${VIRTUAL_ENV}"
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

WORKDIR /socmate

COPY requirements.txt ./
COPY orchestrator/pyproject.toml ./orchestrator/pyproject.toml
RUN pip install --upgrade pip \
 && pip install -r requirements.txt \
 && pip install cocotb cocotb-bus

COPY . /socmate
RUN pip install -e ./orchestrator

# -----------------------------------------------------------------------------
# Sky130 PDK -- install at build time so first pipeline run isn't a 5GB
# download. The pin lives in scripts/pdk-version.env so a bump only needs
# to land in one place (Dockerfile + install_toolchain.sh + CI all read it).
# -----------------------------------------------------------------------------
ENV PDK_ROOT=/socmate/.pdk
RUN pip install volare \
 && . /socmate/scripts/pdk-version.env \
 && volare enable \
        --pdk sky130 \
        --pdk-root "${PDK_ROOT}" \
        "${SKY130_PDK_COMMIT}"

# -----------------------------------------------------------------------------
# Tool wrappers: the openlane base exposes yosys / openroad / magic /
# netgen / klayout / verilator at the bare names on $PATH, so the
# scripts/*-nix.sh wrappers just need to exec the real binary. Override
# config.yaml to point at bare names.
# -----------------------------------------------------------------------------
RUN python3 -c "import yaml, pathlib; \
p = pathlib.Path('orchestrator/config.yaml'); \
c = yaml.safe_load(p.read_text()); \
c['backend']['openroad_binary'] = 'openroad'; \
c['backend']['magic_binary']    = 'magic'; \
c['backend']['netgen_binary']   = 'netgen'; \
c['backend']['yosys_binary']    = 'yosys'; \
c['backend']['klayout_binary']  = 'klayout'; \
p.write_text(yaml.safe_dump(c, sort_keys=False))"

# Make the .socmate / rtl / tb / arch dirs that the pipeline writes to
# world-writable so they survive `docker run --user $(id -u)` style invocations.
RUN mkdir -p /socmate/.socmate /socmate/rtl /socmate/tb /socmate/arch \
             /socmate/syn /socmate/sim_build /socmate/pnr \
 && chmod -R 0777 /socmate/.socmate /socmate/rtl /socmate/tb /socmate/arch \
                  /socmate/syn /socmate/sim_build /socmate/pnr

# Default behaviour: drop into a shell. Set SOCMATE_MODE=pipeline (or
# =mcp, =backend) to launch a specific entry point via the entrypoint.
ENV SOCMATE_MODE=shell
ENV SOCMATE_PROJECT_ROOT=/socmate

ENTRYPOINT ["/socmate/scripts/runpod_entrypoint.sh"]
CMD []
