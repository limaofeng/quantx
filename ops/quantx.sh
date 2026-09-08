#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec "${CONDA_EXE:-conda}" run --no-capture-output --name "${CONDA_ENV_NAME:-quantx}" python "$root/ops/macos_runtime.py" "$@"
