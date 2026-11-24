#!/usr/bin/env bash
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# Legacy project-local mode remains explicitly available for migrations.
for arg in "$@"; do
  if [[ "$arg" == --legacy-project || "$arg" == --target || "$arg" == --target=* ]]; then
    args=(); for item in "$@"; do [[ "$item" == --legacy-project ]] || args+=("$item"); done
    exec python3 "$here/install.py" "${args[@]}"
  fi
done
exec python3 "$here/standalone.py" install "$@"
