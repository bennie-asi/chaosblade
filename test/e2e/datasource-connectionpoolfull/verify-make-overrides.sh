#!/usr/bin/env bash
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
