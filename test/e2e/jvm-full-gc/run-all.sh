#!/usr/bin/env bash

# Copyright 2025 The ChaosBlade Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
