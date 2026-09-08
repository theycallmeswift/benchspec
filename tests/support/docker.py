"""The fake `docker` CLI that every Docker test drives instead of a real daemon.

`write_docker_shim` writes an executable script that answers each `docker` subcommand
from an environment knob, so a test can make `docker exec` echo, sleep, print to stderr,
or exit nonzero — and can read back the exact argv the code under test rendered —
without a Docker daemon anywhere near the suite. The knobs:

- `BENCHSPEC_DOCKER_COMMAND_LOG`: file the shim appends one line of argv to per call.
- `BENCHSPEC_SHIM_INFO_EXIT`: exit status of `info` (default 0, i.e. a live daemon).
- `BENCHSPEC_SHIM_INSPECT_EXIT`: exit status of `image inspect` without `--format`
  (default 1, i.e. the image is absent).
- `BENCHSPEC_SHIM_INSPECT_FORMAT_EXIT`: exit status of `image inspect --format`
  (default 0, printing `sha256:deadbeef`; nonzero prints a daemon-style error instead).
- `BENCHSPEC_SHIM_CONTAINERS`: newline-separated names `ps` prints.
- `BENCHSPEC_SHIM_IMAGES`: newline-separated references `images` prints.
- `BENCHSPEC_SHIM_EXEC`: the shell command `exec` runs in the "guest" (default `true`).
- `BENCHSPEC_SHIM_RM_EXIT`: exit status of `rm` (default 0).
- `BENCHSPEC_SHIM_RM_SLEEP`: seconds `rm` hangs instead of exiting (default 0), for the
  wedged-daemon path — the argv line is logged before the hang, so a caller that gives
  up on the call can still prove it made it.
- `BENCHSPEC_SHIM_PS_EXIT`: exit status of `ps` (default 0, i.e. a reachable daemon).
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

# A payload the test will kill or time out must `exec` its long-running command: `/bin/sh`
# is dash on Linux, which forks rather than exec-optimizing, and an orphaned grandchild
# would keep the pipes open past the shim's own death.
_SHIM_SCRIPT = dedent("""\
    #!/bin/sh
    if [ -n "$BENCHSPEC_DOCKER_COMMAND_LOG" ]; then
        printf '%s\\n' "$*" >> "$BENCHSPEC_DOCKER_COMMAND_LOG"
    fi

    subcommand="$1"
    if [ "$#" -gt 0 ]; then
        shift
    fi

    case "$subcommand" in
        info)
            info_exit="${BENCHSPEC_SHIM_INFO_EXIT:-0}"
            if [ "$info_exit" -ne 0 ]; then
                printf 'Cannot connect to the Docker daemon at unix:///var/run/docker.sock\\n' >&2
                exit "$info_exit"
            fi
            exit 0
            ;;
        image)
            if [ "$1" != "inspect" ]; then
                exit 0
            fi
            for argument in "$@"; do
                case "$argument" in
                    --format*)
                        format_exit="${BENCHSPEC_SHIM_INSPECT_FORMAT_EXIT:-0}"
                        if [ "$format_exit" -ne 0 ]; then
                            printf 'Error: No such image\\n' >&2
                            exit "$format_exit"
                        fi
                        printf 'sha256:deadbeef\\n'
                        exit 0
                        ;;
                esac
            done
            exit "${BENCHSPEC_SHIM_INSPECT_EXIT:-1}"
            ;;
        ps)
            ps_exit="${BENCHSPEC_SHIM_PS_EXIT:-0}"
            if [ "$ps_exit" -ne 0 ]; then
                printf 'Cannot connect to the Docker daemon\\n' >&2
                exit "$ps_exit"
            fi
            if [ -n "$BENCHSPEC_SHIM_CONTAINERS" ]; then
                printf '%s\\n' "$BENCHSPEC_SHIM_CONTAINERS"
            fi
            exit 0
            ;;
        images)
            if [ -n "$BENCHSPEC_SHIM_IMAGES" ]; then
                printf '%s\\n' "$BENCHSPEC_SHIM_IMAGES"
            fi
            exit 0
            ;;
        exec)
            exec sh -c "${BENCHSPEC_SHIM_EXEC:-true}"
            ;;
        rm)
            rm_sleep="${BENCHSPEC_SHIM_RM_SLEEP:-0}"
            if [ "$rm_sleep" != "0" ]; then
                exec sleep "$rm_sleep"
            fi
            exit "${BENCHSPEC_SHIM_RM_EXIT:-0}"
            ;;
        *)
            exit 0
            ;;
    esac
""")


def write_docker_shim(shim_dir: Path) -> Path:
    """Write the executable `docker` shim into `shim_dir` and return its path.

    Args:
        shim_dir: Directory to hold the shim; created with its parents when missing.

    Returns:
        The path to the executable `docker` script.
    """
    shim_dir.mkdir(parents=True, exist_ok=True)

    shim = shim_dir / "docker"
    shim.write_text(_SHIM_SCRIPT, encoding="utf-8")
    shim.chmod(0o755)

    return shim
