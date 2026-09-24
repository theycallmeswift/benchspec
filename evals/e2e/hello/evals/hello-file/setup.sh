#!/usr/bin/env bash
set -e
case "$BENCHSPEC_ARM" in
  baseline) exit 0 ;;                      # installs nothing — measures what the agent already knows
  placebo)  src=../../placebo/SKILL.md ;;  # same name and description, noop body
  *)        src=../../SKILL.md ;;
esac
mkdir -p /home/benchspec/skills/hello
cp "$src" /home/benchspec/skills/hello/SKILL.md
