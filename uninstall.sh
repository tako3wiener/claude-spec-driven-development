#!/usr/bin/env bash
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
for arg in "$@"; do
  if [[ "$arg" == --legacy-project || "$arg" == --target || "$arg" == --target=* || "$arg" == --baseline || "$arg" == --baseline=* ]]; then
    args=(); for item in "$@"; do [[ "$item" == --legacy-project ]] || args+=("$item"); done
    exec python3 "$here/install.py" --uninstall "${args[@]}"
  fi
done
exec python3 "$here/standalone.py" uninstall "$@"
