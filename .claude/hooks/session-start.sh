#!/bin/bash
# Prepare a Claude Code on the web session so `make e2e` can run. No-op elsewhere.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

DOCKERD_LOG=/tmp/benchspec-dockerd.log
PROXY_CA_BUNDLE=/root/.ccr/ca-bundle.crt
PROXY_CA_IMAGE=benchspec-base:proxy-ca

start_docker_daemon() {
  # The cloud VM ships dockerd but nothing starts it; setsid keeps it alive after the
  # hook's process group exits.
  if docker info >/dev/null 2>&1; then
    return 0
  fi

  setsid nohup dockerd >"$DOCKERD_LOG" 2>&1 </dev/null &

  for _ in $(seq 1 30); do
    if docker info >/dev/null 2>&1; then
      echo "started dockerd (log: $DOCKERD_LOG)"
      return 0
    fi
    sleep 1
  done

  echo "dockerd did not come up within 30s; see $DOCKERD_LOG" >&2
  return 1
}

build_proxy_ca_base_image() {
  # The VM's security proxy intercepts TLS inside containers too. Without its CA the
  # snapshot build's `curl https://claude.ai/install.sh | bash` fetches nothing and the
  # sandbox ends up with no `claude`, so snapshots here build from a base that trusts it.
  if [ ! -f "$PROXY_CA_BUNDLE" ]; then
    return 0
  fi

  if docker image inspect "$PROXY_CA_IMAGE" >/dev/null 2>&1; then
    return 0
  fi

  local build_dir
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
}

point_benchspec_at_proxy_ca_image() {
  if [ -z "${CLAUDE_ENV_FILE:-}" ]; then
    return 0
  fi

  echo "export BENCHSPEC_BASE_IMAGE=$PROXY_CA_IMAGE" >>"$CLAUDE_ENV_FILE"
}

if ! start_docker_daemon; then
  echo "make e2e needs a running Docker daemon; skipping the sandbox base image" >&2
  exit 0
fi

if [ -f "$PROXY_CA_BUNDLE" ]; then
  build_proxy_ca_base_image
  point_benchspec_at_proxy_ca_image
fi
