#!/bin/bash
# Setup script for the Claude Code on the web cloud environment that runs this repo.
#
# Paste it into the environment's "Setup script" field at claude.ai/code. It runs once
# as root, then the filesystem is snapshotted: files and Docker images carry over to
# later sessions, running processes do not. See docs/sandbox.md, "Claude Code on the web".
#
# A non-zero exit fails the session start, so every step past the harness install warns
# and continues rather than aborting: a session without `make e2e` beats no session.
set -euo pipefail

HOST_CA_DIR=/usr/local/share/ca-certificates
PROXY_CA_IMAGE=benchspec-base:proxy-ca
DOTENV=/home/user/.env
CLONE=/home/user/benchspec
DOCKERD_LOG=/tmp/benchspec-dockerd.log
# Starts dockerd if it is not answering, then waits for it. Shared by this script
# and the session hook below, so both start the daemon the same way.
START_DOCKERD="docker info >/dev/null 2>&1 || { setsid nohup dockerd >$DOCKERD_LOG 2>&1 </dev/null & for _ in \$(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done; }"

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

# The snapshot keeps files, not processes, so every later session starts with dockerd
# stopped. A SessionStart hook in the clone's gitignored local settings starts it on
# each session, so `make e2e` needs no manual step.
if [ -d "$CLONE" ]; then
  mkdir -p "$CLONE/.claude"
  cat >"$CLONE/.claude/settings.local.json" <<EOF
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "$START_DOCKERD" }
        ]
      }
    ]
  }
}
EOF
else
  echo "no clone at $CLONE; skipping the dockerd session hook" >&2
fi

# Start Docker. The VM ships dockerd but nothing launches it.
bash -c "$START_DOCKERD"
if ! docker info >/dev/null 2>&1; then
  echo "dockerd did not start; see $DOCKERD_LOG. Skipping $PROXY_CA_IMAGE." >&2
  exit 0
fi

# Build the sandbox base image. The VM's egress gateway intercepts TLS inside containers
# too; without its CA the snapshot build's `curl https://claude.ai/install.sh | bash`
# fetches nothing and the sandbox ends up with no `claude`. The gateway's CA is among the
# CAs baked into the VM's local trust directory at image build time, which is why that
# directory and not the agent proxy's bundle (absent until Claude Code launches) is the
# source. Every CA there is Anthropic's own sandbox infrastructure, so all of them go in.
if ! ls "$HOST_CA_DIR"/*.crt >/dev/null 2>&1; then
  echo "no CAs under $HOST_CA_DIR; sandbox snapshots will not build. Skipping $PROXY_CA_IMAGE." >&2
  exit 0
fi

build_dir=$(mktemp -d)
mkdir "$build_dir/ca-certificates"
cp "$HOST_CA_DIR"/*.crt "$build_dir/ca-certificates/"
cat >"$build_dir/Dockerfile" <<'DOCKERFILE'
FROM ubuntu:latest
COPY ca-certificates/ /usr/local/share/ca-certificates/
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && update-ca-certificates \
 && rm -rf /var/lib/apt/lists/*
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
DOCKERFILE
if docker build --quiet --tag "$PROXY_CA_IMAGE" "$build_dir" >/dev/null; then
  echo "built $PROXY_CA_IMAGE"
else
  echo "docker build of $PROXY_CA_IMAGE failed; sandbox snapshots will not build" >&2
fi
rm -rf "$build_dir"
