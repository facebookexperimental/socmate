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
# Base: efabless/openlane already has OpenROAD, Magic, netgen, KLayout, Yosys,
# and the Sky130 PDK pinned at known-good versions, plus a working tcl/tk env.
# We layer Python 3.11, Verilator, cocotb, Node + Claude CLI on top.
# -----------------------------------------------------------------------------
FROM efabless/openlane:2024.10.18 AS socmate

ARG DEBIAN_FRONTEND=noninteractive
ARG NODE_MAJOR=20

# OpenLane's image is non-root (UID 1000); the apt-get steps below need root.
USER root

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git gnupg make sudo \
        python3.11 python3.11-venv python3-pip \
        verilator \
    && curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

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
# Tool wrappers: inside the container the EDA tools are on $PATH (provided by
# the openlane base image), so the scripts/*-nix.sh wrappers just need to
# exec the real binary. We override config.yaml to point at bare names.
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
