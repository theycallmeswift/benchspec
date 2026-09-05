#!/usr/bin/env bash
set -e
if [ "$BENCHSPEC_ARM" = "baseline" ]; then
  exit 0   # baseline installs nothing — measures what the agent already knows
fi
mkdir -p /home/benchspec/skills/hello
cp ../../SKILL.md /home/benchspec/skills/hello/SKILL.md
