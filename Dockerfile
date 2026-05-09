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
# Node + the Claude CLI on top via the image's bundled Nix.
#
# All efabless/openlane* images are Nix-only -- there is no Debian-based
# variant to fall back on. Earlier builds tried patching the glibc ELF
# (`@anthropic-ai/claude-code-linux-x64`); it segfaulted because the
# Bun-compiled binary's glibc ABI assumptions don't survive the move
# from FHS to nix-store paths even after patchelf. The musl variant
# (`@anthropic-ai/claude-code-linux-x64-musl`) is dynamically linked
# against /lib/ld-musl-x86_64.so.1, which on musl is also the libc --
# one symlink to nixpkgs.musl resolves the entire dependency, no
# patchelf needed.
# -----------------------------------------------------------------------------
FROM ghcr.io/efabless/openlane2:2.3.10 AS socmate

RUN nix-channel --add https://nixos.org/channels/nixos-24.05 nixpkgs \
 && nix-channel --update \
 && nix-env -iA \
        nixpkgs.nodejs_20 \
        nixpkgs.gnumake \
        nixpkgs.openssh \
        nixpkgs.musl

ENV PATH="/root/.nix-profile/bin:${PATH}"

# Make the musl ELF interpreter resolvable at the FHS path the binary
# is hard-linked against. nix's musl ships ld-musl-x86_64.so.1 which is
# also the libc; one symlink covers both. Without this, every musl
# binary on this image dies with the kernel's "required file not found".
RUN MUSL_LD="$(find /nix/store -maxdepth 4 -name 'ld-musl-x86_64.so.1' \
        \( -type f -o -type l \) 2>/dev/null | head -1)" \
 && test -n "${MUSL_LD}" \
 && mkdir -p /lib \
 && ln -sf "${MUSL_LD}" /lib/ld-musl-x86_64.so.1 \
 && echo "musl_ld=${MUSL_LD}"

# Nix-built Node sets npm's default prefix into the read-only Nix store,
# so `npm install -g` "succeeds" but the bin can't symlink anywhere on
# PATH. Pin the prefix to a writable dir we explicitly put on PATH.
ENV NPM_CONFIG_PREFIX=/opt/npm-global
ENV PATH="/opt/npm-global/bin:${PATH}"

# Install the wrapper package + the Linux-x64-musl native variant
# explicitly. Setting `--include=optional` and listing the musl
# subpackage by name forces the right binary regardless of what
# install.cjs's runtime detection thinks (Nix Node is glibc-built so
# `process.report` would otherwise pick the glibc variant).
RUN mkdir -p /opt/npm-global \
 && npm install -g \
        @anthropic-ai/claude-code \
        @anthropic-ai/claude-code-linux-x64-musl

# `npm install -g`'s postinstall on the wrapper package may have copied
# the glibc binary over bin/claude.exe (because Nix Node is glibc-
# built). Force-replace bin/claude with a symlink to the musl-variant's
# native binary so we use the version that actually runs on this image.
RUN set -eux \
 && MUSL_BIN=/opt/npm-global/lib/node_modules/@anthropic-ai/claude-code-linux-x64-musl/claude \
 && test -x "${MUSL_BIN}" \
 && ln -sf "${MUSL_BIN}" /opt/npm-global/bin/claude

# Capture the resolved Claude CLI path at build time and bake it as
# CLAUDE_CLI_PATH so runtime resolution can't drift if PATH changes
# under us. Also fail the build loud if `claude --version` fails -- the
# runtime "PermissionError: ''" failure mode is much harder to debug.
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
RUN mkdir -p /etc/ssh /var/run/sshd /run/sshd /root/.ssh \
 && chmod 700 /root/.ssh \
 && ssh-keygen -A \
 && { \
        echo "Port 22"; \
        echo "PermitRootLogin prohibit-password"; \
        echo "PasswordAuthentication no"; \
        echo "PubkeyAuthentication yes"; \
        echo "AuthorizedKeysFile /root/.ssh/authorized_keys"; \
        echo "ChallengeResponseAuthentication no"; \
        echo "UsePAM no"; \
        echo "PrintMotd no"; \
        echo "AcceptEnv LANG LC_*"; \
    } > /etc/ssh/sshd_config

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
