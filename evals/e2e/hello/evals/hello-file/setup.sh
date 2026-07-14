#!/usr/bin/env bash
set -e
if [ "$EVALSPEC_ARM" = "baseline" ]; then
  exit 0   # baseline installs nothing — measures what the agent already knows
fi
mkdir -p /home/evalspec/skills/hello
cp ../../SKILL.md /home/evalspec/skills/hello/SKILL.md
