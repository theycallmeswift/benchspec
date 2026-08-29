#!/usr/bin/env bash
set -e
if [ "$HARNESSBENCH_ARM" = "baseline" ]; then
  exit 0   # baseline installs nothing — measures what the agent already knows
fi
mkdir -p /home/harnessbench/skills/hello
cp ../../SKILL.md /home/harnessbench/skills/hello/SKILL.md
