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
# shellcheck disable=SC1091
# lib.sh is resolved next to this script at runtime.
source "$SCRIPT_DIR/lib.sh"

if [[ ${1:-} == "--self-test" ]]; then
  require_command bash
  require_command jq
  bash -n "$SCRIPT_DIR/lib.sh" "$SCRIPT_DIR/build-and-deploy.sh" "$SCRIPT_DIR/run-e2e.sh" \
    "$SCRIPT_DIR/verify-make-overrides.sh"
  assert_json '{"success":true,"result":{"statuses":[{"result":{"state":"ACTIVE","actualHold":4}}]}}' \
    '.success and any(.result.statuses[]; .result.state == "ACTIVE" and .result.actualHold == 4)' \
    "structured ACTIVE gate rejected valid input"
  assert_json '{"success":false,"result":{"statuses":[{"state":"ROLLED_BACK"}]}}' \
    '(.success | not) and any(.result.statuses[]; .state == "ROLLED_BACK")' \
    "rollback gate rejected valid input"
  if command -v shellcheck >/dev/null 2>&1; then
    shellcheck "$SCRIPT_DIR/lib.sh" "$SCRIPT_DIR/build-and-deploy.sh" \
      "$SCRIPT_DIR/run-e2e.sh" "$SCRIPT_DIR/verify-make-overrides.sh"
  fi
  echo "PASS: datasource E2E script gates"
  exit 0
fi

for command in jq curl kubectl; do
  require_command "$command"
done
KUBECONFIG_PATH=${KUBECONFIG_PATH:-/etc/rancher/k3s/k3s.yaml}
NAMESPACE=${NAMESPACE:-chaosblade-e2e}
HIKARI_BASE_URL=${HIKARI_BASE_URL:-http://127.0.0.1:30081}
DRUID_BASE_URL=${DRUID_BASE_URL:-http://127.0.0.1:30082}
TTL_SECONDS=${TTL_SECONDS:-60}
if [[ -z ${BLADE:-} ]]; then
  test -n "${BLADE_HOME:-}" || fail "set BLADE or BLADE_HOME to an unpacked chaosblade distribution"
  BLADE="$BLADE_HOME/blade"
fi
test -x "$BLADE" || fail "blade executable not found: $BLADE"

declare -a EXPERIMENT_UIDS=()
cleanup() {
  set +e
  for uid in "${EXPERIMENT_UIDS[@]}"; do
    "$BLADE" destroy "$uid" --target k8s --kubeconfig "$KUBECONFIG_PATH" >/dev/null 2>&1
    kubectl --kubeconfig "$KUBECONFIG_PATH" delete chaosblade "$uid" --ignore-not-found --wait=false >/dev/null 2>&1
  done
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" \
    delete pod datasource-invalid --ignore-not-found --force --grace-period=0 --wait=false >/dev/null 2>&1
  local pod
  pod=$(kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" \
    get pod -l app=datasource-hikari -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
  if [[ -n $pod ]]; then
    kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" label pod "$pod" scenario- >/dev/null 2>&1
  fi
}
trap cleanup EXIT

create_pool_experiment() {
  local labels=$1
  local timeout=$2
  local output status
  if output=$("$BLADE" create k8s container-datasource connectionpoolfull \
    --labels "$labels" --container-names app --namespace "$NAMESPACE" \
    --kubeconfig "$KUBECONFIG_PATH" --data-source-name coreDataSource \
    --target-percent 100 --timeout "$timeout" --pid 1 --javaHome /opt/java \
    --chaosblade-override 2>&1); then
    status=0
  else
    status=$?
  fi
  printf '%s\n' "$output" | tail -n 1
  return "$status"
}

run_explicit_destroy() {
  local response uid resource
  response=$(create_pool_experiment app=datasource-hikari 60)
  assert_json "$response" '.success == true' "Hikari create failed"
  uid=$(json_value "$response" '.result.uid')
  EXPERIMENT_UIDS+=("$uid")
  resource=$(kubectl --kubeconfig "$KUBECONFIG_PATH" get chaosblade "$uid" -o json)
  assert_json "$resource" \
    'any(.status.expStatuses[0].resStatuses[]; .result.state == "ACTIVE" and .result.poolType == "HIKARI" and .result.actualHold == 4)' \
    "Hikari structured CR result missing"
  wait_for_pool_active "$HIKARI_BASE_URL/pool" 4 20 >/dev/null
  test "$(http_status "$HIKARI_BASE_URL/query")" = 503 || fail "Hikari query did not fail while pool was full"
  "$BLADE" destroy "$uid" --target k8s --kubeconfig "$KUBECONFIG_PATH" >/dev/null
  wait_for_pool_active "$HIKARI_BASE_URL/pool" 0 30 >/dev/null
  wait_for_http "$HIKARI_BASE_URL/query" 200 30
  echo "PASS: Hikari explicit destroy"
}

run_ttl_release() {
  local response uid resource
  response=$(create_pool_experiment app=datasource-druid "$TTL_SECONDS")
  assert_json "$response" '.success == true' "Druid create failed"
  uid=$(json_value "$response" '.result.uid')
  EXPERIMENT_UIDS+=("$uid")
  resource=$(kubectl --kubeconfig "$KUBECONFIG_PATH" get chaosblade "$uid" -o json)
  assert_json "$resource" \
    'any(.status.expStatuses[0].resStatuses[]; .result.state == "ACTIVE" and .result.poolType == "DRUID" and .result.actualHold == 4)' \
    "Druid structured CR result missing"
  wait_for_pool_active "$DRUID_BASE_URL/pool" 4 20 >/dev/null
  test "$(http_status "$DRUID_BASE_URL/query")" = 503 || fail "Druid query did not fail while pool was full"
  wait_for_pool_active "$DRUID_BASE_URL/pool" 0 "$((TTL_SECONDS + 30))" >/dev/null
  wait_for_http "$DRUID_BASE_URL/query" 200 30
  echo "PASS: Druid hard TTL"
}

run_mixed_compensation() {
  local pod output status uid resource
  pod=$(kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" \
    get pod -l app=datasource-hikari -o jsonpath='{.items[0].metadata.name}')
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" \
    label pod "$pod" scenario=pool-mixed --overwrite >/dev/null
  kubectl --kubeconfig "$KUBECONFIG_PATH" apply -f "$SCRIPT_DIR/k8s-mixed.yaml" >/dev/null
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" \
    wait --for=condition=Ready pod/datasource-invalid --timeout=60s >/dev/null
  set +e
  output=$(create_pool_experiment scenario=pool-mixed 60)
  status=$?
  set -e
  test "$status" -ne 0 || fail "mixed create unexpectedly succeeded"
  assert_json "$output" '(.success | not)' "mixed create did not report failure"
  uid=$(json_value "$output" '.result.uid')
  EXPERIMENT_UIDS+=("$uid")
  resource=$(kubectl --kubeconfig "$KUBECONFIG_PATH" get chaosblade "$uid" -o json)
  assert_json "$resource" \
    'any(.status.expStatuses[0].resStatuses[]; .state == "ROLLED_BACK")' \
    "successful mixed target was not rolled back"
  wait_for_pool_active "$HIKARI_BASE_URL/pool" 0 30 >/dev/null
  wait_for_http "$HIKARI_BASE_URL/query" 200 30
  echo "PASS: mixed-target atomic compensation"
}

wait_for_http "$HIKARI_BASE_URL/query" 200 60
wait_for_http "$DRUID_BASE_URL/query" 200 60
run_explicit_destroy
run_ttl_release
run_mixed_compensation
wait_for_pool_active "$HIKARI_BASE_URL/pool" 0 30 >/dev/null
wait_for_pool_active "$DRUID_BASE_URL/pool" 0 30 >/dev/null
echo "PASS: datasource connection-pool exhaustion E2E"
