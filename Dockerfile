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
# Base: ghcr.io/efabless/openlane2 is a Nix-built image (~1.5 GB compressed)
# that bundles Yosys, OpenROAD, Magic, netgen, KLayout, Verilator, iverilog
# and OpenSTA already on PATH, plus a Python 3.11 environment. We layer
# Node + the Claude CLI on top via the official Node tarball (the image has
# no apt) and a per-project venv for the orchestrator deps.
#
# Why not the IIC-OSIC-TOOLS or efabless/openlane:1.x image? They're 5 GB+
# compressed, ~15 GB extracted, and overflow the 32 GB Codespaces disk
# during `docker buildx build` -- the build then falls through to the
# alpine recovery container.
# -----------------------------------------------------------------------------
FROM ghcr.io/efabless/openlane2:2.3.10 AS socmate

# The image already runs as root with /nix/store on PATH, so we don't need
# USER/WORKDIR juggling. We *do* need Node + npm for the Claude CLI;
# openlane2 doesn't bundle Node, so we install via the image's bundled
# Nix (a multi-stage COPY from node:20-slim doesn't work because that
# binary needs /lib/x86_64-linux-gnu/libc.so.6 which the Nix-only base
# doesn't provide).
#
# `nix-env -iA nixpkgs.nodejs_20` installs into /root/.nix-profile/bin;
# we add a channel first because the openlane2 image is built declaratively
# without a default `nixpkgs` user channel.
RUN nix-channel --add https://nixos.org/channels/nixos-24.05 nixpkgs \
 && nix-channel --update \
 && nix-env -iA nixpkgs.nodejs_20 nixpkgs.gnumake

ENV PATH="/root/.nix-profile/bin:${PATH}"

RUN npm install -g @anthropic-ai/claude-code

# -----------------------------------------------------------------------------
# Python venv + socmate deps. Done in two layers so a code-only edit
# doesn't bust the dependency cache. The base image's python3 is 3.11.9 so
# we use it directly (no deadsnakes / system Python dance).
# -----------------------------------------------------------------------------
ENV VIRTUAL_ENV=/opt/socmate-venv
RUN python3 -m venv "${VIRTUAL_ENV}"
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
# Tool wrappers: openlane2 already exposes yosys / openroad / magic / netgen
# / klayout / verilator at the bare names on $PATH, so the scripts/*-nix.sh
# wrappers just need to exec the real binary. We override config.yaml to
# point at bare names.
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
