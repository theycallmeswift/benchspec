#!/bin/bash
# Setup script for the Claude Code on the web cloud environment that runs this repo.
#
# Paste it into the environment's "Setup script" field at claude.ai/code. It runs once
# as root, then the filesystem is snapshotted: files and Docker images carry over to
# later sessions, running processes do not. See docs/sandbox.md, "Claude Code on the web".
set -euo pipefail

PROXY_CA_BUNDLE=/root/.ccr/ca-bundle.crt
PROXY_CA_IMAGE=benchspec-base:proxy-ca
DOTENV=/home/user/.env

# Install harnesses. `claude` is preinstalled; the judge runs on Codex.
npm install -g @openai/codex || true

# Set up the env. Claude Code strips CLAUDE_CODE_OAUTH_TOKEN from every command it runs,
# so the environment stores the token as BENCHSPEC_CLAUDE_OAUTH_TOKEN and benchspec's
# dotenv loader, walking up from the clone, expands the reference at run time. The file
# holds no secret. BENCHSPEC_BASE_IMAGE selects the proxy-CA base built below.
cat >"$DOTENV" <<EOF
CLAUDE_CODE_OAUTH_TOKEN=\${BENCHSPEC_CLAUDE_OAUTH_TOKEN}
BENCHSPEC_BASE_IMAGE=$PROXY_CA_IMAGE
EOF

# Start Docker. The VM ships dockerd but nothing launches it.
if ! docker info >/dev/null 2>&1; then
  setsid nohup dockerd >/tmp/benchspec-dockerd.log 2>&1 </dev/null &
  for _ in $(seq 1 30); do
    docker info >/dev/null 2>&1 && break
    sleep 1
  done
fi
docker info >/dev/null 2>&1 || { echo "dockerd did not start; see /tmp/benchspec-dockerd.log" >&2; exit 1; }

# Build the sandbox base image. The VM's security proxy intercepts TLS inside containers
# too; without its CA the snapshot build's `curl https://claude.ai/install.sh | bash`
# fetches nothing and the sandbox ends up with no `claude`.
if [ ! -f "$PROXY_CA_BUNDLE" ]; then
  echo "proxy CA bundle missing at $PROXY_CA_BUNDLE; sandbox snapshots will not build" >&2
  exit 1
fi

build_dir=$(mktemp -d)
cp "$PROXY_CA_BUNDLE" "$build_dir/proxy-ca.crt"
cat >"$build_dir/Dockerfile" <<'DOCKERFILE'
FROM ubuntu:latest
COPY proxy-ca.crt /usr/local/share/ca-certificates/proxy-ca.crt
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && update-ca-certificates \
 && rm -rf /var/lib/apt/lists/*
ENV NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/proxy-ca.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
DOCKERFILE
docker build --quiet --tag "$PROXY_CA_IMAGE" "$build_dir" >/dev/null
rm -rf "$build_dir"
echo "built $PROXY_CA_IMAGE"
