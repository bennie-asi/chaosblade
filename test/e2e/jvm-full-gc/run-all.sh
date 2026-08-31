#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd "$SCRIPT_DIR/../../.." && pwd)
DATASOURCE_E2E_DIR="$ROOT_DIR/test/e2e/datasource-connectionpoolfull"

"$DATASOURCE_E2E_DIR/build-and-deploy.sh"

if [[ -z ${BLADE:-} ]]; then
  BLADE_PACKAGE=${BLADE_PACKAGE:-$(find "$ROOT_DIR/target" -maxdepth 1 -type d \
    -name "chaosblade-*linux_amd64" -print -quit)}
  BLADE="$BLADE_PACKAGE/blade"
fi

BLADE="$BLADE" "$SCRIPT_DIR/run-e2e.sh"
