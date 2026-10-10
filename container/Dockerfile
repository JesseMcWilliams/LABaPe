# LABaPe: one image with the web app, the job worker and the whole
# toolchain (Claude_Docs/Planning_Web-Interface-Design.md §14).
#
#   docker build -f container/Dockerfile -t labape:local .     (from the repo root)
#
# Toolchain versions are pinned here and in the matching ARGs; bump them
# together with the CLI host's versions (README "Requirements").

# --- web UI --------------------------------------------------------------
FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# --- runtime -------------------------------------------------------------
FROM debian:trixie-slim

ARG TOFU_VERSION=1.12.6
ARG PACKER_VERSION=1.16.1
ARG ANSIBLE_CORE_VERSION=2.21.4

ENV DEBIAN_FRONTEND=noninteractive \
    PATH=/opt/labape/venv/bin:$PATH \
    TF_CLI_CONFIG_FILE=/opt/labape/tofurc \
    ANSIBLE_COLLECTIONS_PATH=/usr/share/ansible/collections \
    PACKER_PLUGIN_PATH=/opt/labape/packer-plugins \
    PYTHONUNBUFFERED=1

# libvirt-clients/virtinst/qemu-utils/xorriso: the VM module's scripts.
# perl: create-from-template.sh. iputils-ping: check-network.sh (arping
# is left out on purpose: inside a container network it can't see the
# LAN, so it would report every address free).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      bash ca-certificates curl unzip git openssh-client perl iputils-ping \
      python3 python3-venv \
      libvirt-clients virtinst qemu-utils xorriso \
 && rm -rf /var/lib/apt/lists/*

# OpenTofu and Packer, checksum-verified.
RUN set -eux; cd /tmp; \
    curl -fsSLO "https://github.com/opentofu/opentofu/releases/download/v${TOFU_VERSION}/tofu_${TOFU_VERSION}_linux_amd64.zip"; \
    curl -fsSLO "https://github.com/opentofu/opentofu/releases/download/v${TOFU_VERSION}/tofu_${TOFU_VERSION}_SHA256SUMS"; \
    grep " tofu_${TOFU_VERSION}_linux_amd64.zip\$" "tofu_${TOFU_VERSION}_SHA256SUMS" | sha256sum -c -; \
    unzip -o "tofu_${TOFU_VERSION}_linux_amd64.zip" tofu -d /usr/local/bin; \
    curl -fsSLO "https://releases.hashicorp.com/packer/${PACKER_VERSION}/packer_${PACKER_VERSION}_linux_amd64.zip"; \
    curl -fsSLO "https://releases.hashicorp.com/packer/${PACKER_VERSION}/packer_${PACKER_VERSION}_SHA256SUMS"; \
    grep " packer_${PACKER_VERSION}_linux_amd64.zip\$" "packer_${PACKER_VERSION}_SHA256SUMS" | sha256sum -c -; \
    unzip -o "packer_${PACKER_VERSION}_linux_amd64.zip" packer -d /usr/local/bin; \
    rm -f /tmp/*.zip /tmp/*SHA256SUMS; \
    tofu version; packer version

# Python: the app, Ansible (+ pywinrm for Windows hosts) in one venv that
# the scripts' `python3` resolves to (PATH above). It sees the system
# packages because virt-install (`#!/usr/bin/env python3`) needs Debian's
# python3-gi and python3-libvirt.
COPY app/pyproject.toml /opt/labape/src/app/pyproject.toml
COPY app/labape /opt/labape/src/app/labape
RUN python3 -m venv --system-site-packages /opt/labape/venv \
 && pip install --no-cache-dir "ansible-core==${ANSIBLE_CORE_VERSION}" pywinrm /opt/labape/src/app \
 && ansible-galaxy collection install -p /usr/share/ansible/collections \
      ansible.windows:==3.8.0 microsoft.ad:==1.12.1 community.general:==13.4.0 chocolatey.chocolatey:==1.6.0

# The engine: the same scripts the CLI runs, copied as a snapshot.
COPY scripts /opt/labape/engine/scripts
COPY tofu /opt/labape/engine/tofu
COPY ansible /opt/labape/engine/ansible
COPY packer /opt/labape/engine/packer
COPY iso /opt/labape/engine/iso

# OpenTofu providers and Packer plugins baked in, so jobs don't depend on
# the public registries being reachable (decision: central repository
# configuration, design §16, later points these at a local mirror).
RUN set -eux; \
    cd /opt/labape/engine/tofu/backends/libvirt; \
    TF_CLI_CONFIG_FILE= tofu init -backend=false -input=false >/dev/null; \
    TF_CLI_CONFIG_FILE= tofu providers mirror /opt/labape/tofu-mirror; \
    rm -rf .terraform .terraform.lock.hcl ../../modules/*/*/.terraform; cd /; \
    printf '%s\n' \
      'provider_installation {' \
      '  filesystem_mirror {' \
      '    path    = "/opt/labape/tofu-mirror"' \
      '    include = ["registry.opentofu.org/*/*"]' \
      '  }' \
      '  direct {' \
      '    exclude = ["registry.opentofu.org/*/*"]' \
      '  }' \
      '}' > /opt/labape/tofurc; \
    packer plugins install github.com/hashicorp/qemu; \
    packer plugins install github.com/hashicorp/ansible; \
    chmod -R a+rX /opt/labape

COPY --from=web /web/dist /opt/labape/web

VOLUME ["/var/lib/labape"]
EXPOSE 8000
CMD ["labape", "serve"]
