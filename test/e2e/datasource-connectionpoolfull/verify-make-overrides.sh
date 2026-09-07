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

make_value() {
  local name=$1
  shift
  make --no-print-directory -f "$ROOT_DIR/Makefile" -pRrq "$@" : 2>/dev/null |
    awk -v name="$name" '$1 == name && ($2 == "=" || $2 == ":=") {sub(/^[^=]*=[[:space:]]*/, ""); print; exit}'
}

test "$(make_value BLADE_EXEC_JVM_PROJECT)" = "https://github.com/chaosblade-io/chaosblade-exec-jvm.git"
test "$(make_value BLADE_EXEC_JVM_BRANCH)" = "v1.8.0"
test "$(make_value BLADE_OPERATOR_PROJECT)" = "https://github.com/chaosblade-io/chaosblade-operator.git"
test "$(make_value BLADE_OPERATOR_BRANCH)" = "v1.8.0"

test "$(make_value BLADE_EXEC_JVM_PROJECT BLADE_EXEC_JVM_PROJECT=file:///tmp/jvm)" = "file:///tmp/jvm"
test "$(make_value BLADE_EXEC_JVM_BRANCH BLADE_EXEC_JVM_BRANCH=feature/jvm)" = "feature/jvm"
test "$(make_value BLADE_OPERATOR_PROJECT BLADE_OPERATOR_PROJECT=file:///tmp/operator)" = "file:///tmp/operator"
test "$(make_value BLADE_OPERATOR_BRANCH BLADE_OPERATOR_BRANCH=feature/operator)" = "feature/operator"

if make --no-print-directory -f "$ROOT_DIR/Makefile" -n help \
    BLADE_EXEC_JVM_PROJECT="file:///tmp/path with spaces" >/dev/null 2>&1; then
  echo "expected a project path containing spaces to be rejected" >&2
  exit 1
fi

echo "PASS: JVM and Operator repository overrides preserve defaults and accept explicit values"
