#!/usr/bin/env bash
set -euo pipefail

dev_dir=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
exec python3 "$dev_dir/helpers/dev-runner.py" "$dev_dir" "$@"
