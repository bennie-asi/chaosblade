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
# shellcheck disable=SC1091
source "$ROOT_DIR/test/e2e/datasource-connectionpoolfull/lib.sh"

load_summary() {
  local output=$1 target=$2 total errors success p95_index p95
  total=$(awk 'NF >= 2 { count++ } END { print count + 0 }' "$output")
  errors=$(awk 'NF >= 2 && $1 != 200 { count++ } END { print count + 0 }' "$output")
  success=$((total - errors))
  if ((success > 0)); then
    p95_index=$(((success * 95 + 99) / 100))
    p95=$(awk '$1 == 200 { print $2 }' "$output" | sort -n | sed -n "${p95_index}p")
  else
    p95=0
  fi
  jq -cn --arg target "$target" --argjson total "$total" --argjson errors "$errors" \
    --argjson p95 "$p95" '{target:$target,total:$total,errors:$errors,p95Seconds:$p95}'
}

experiment_uid() {
  local response=$1
  jq -er 'if (.result | type) == "object" then .result.uid else .result end' <<<"$response"
}

if [[ ${1:-} == "--self-test" ]]; then
  require_command bash
  require_command jq
  bash -n "$SCRIPT_DIR/run-all.sh" "$SCRIPT_DIR/run-e2e.sh"
  printf '200 0.010\n200 0.030\n503 0.020\n' >"${TMPDIR:-/tmp}/full-gc-load.$$"
  summary=$(load_summary "${TMPDIR:-/tmp}/full-gc-load.$$" "self-test")
  rm -f "${TMPDIR:-/tmp}/full-gc-load.$$"
  assert_json "$summary" \
    '.total == 3 and .errors == 1 and .p95Seconds == 0.03' \
    "load summary gate rejected valid input"
  test "$(experiment_uid '{"success":true,"result":"string-uid"}')" = "string-uid"
  test "$(experiment_uid '{"success":true,"result":{"uid":"object-uid"}}')" = "object-uid"
  if command -v shellcheck >/dev/null 2>&1; then
    shellcheck "$SCRIPT_DIR/run-all.sh" "$SCRIPT_DIR/run-e2e.sh"
  fi
  echo "PASS: JVM Full GC E2E script gates"
  exit 0
fi

for command in awk curl jq kubectl sort; do
  require_command "$command"
done

KUBECONFIG_PATH=${KUBECONFIG_PATH:-/etc/rancher/k3s/k3s.yaml}
NAMESPACE=${NAMESPACE:-chaosblade-e2e}
HIKARI_BASE_URL=${HIKARI_BASE_URL:-http://127.0.0.1:30081}
DRUID_BASE_URL=${DRUID_BASE_URL:-http://127.0.0.1:30082}
LOAD_REQUESTS=${LOAD_REQUESTS:-120}
LOAD_CONCURRENCY=${LOAD_CONCURRENCY:-12}
if [[ -z ${BLADE:-} ]]; then
  test -n "${BLADE_HOME:-}" || fail "set BLADE or BLADE_HOME to an unpacked chaosblade distribution"
  BLADE="$BLADE_HOME/blade"
fi
test -x "$BLADE" || fail "blade executable not found: $BLADE"

declare -a EXPERIMENT_UIDS=()
declare -a LOAD_FILES=()

cleanup() {
  set +e
  local uid
  for uid in "${EXPERIMENT_UIDS[@]}"; do
    "$BLADE" destroy "$uid" --target k8s --kubeconfig "$KUBECONFIG_PATH" >/dev/null 2>&1
    kubectl --kubeconfig "$KUBECONFIG_PATH" delete chaosblade "$uid" \
      --ignore-not-found --wait=false >/dev/null 2>&1
  done
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" label pod \
    -l scenario=full-gc-cluster scenario- >/dev/null 2>&1
  rm -f "${LOAD_FILES[@]}"
}
trap cleanup EXIT

pod_by_label() {
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" get pod -l "$1" \
    -o jsonpath='{.items[0].metadata.name}'
}

fgc_count() {
  local pod=$1
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" exec "$pod" -c app -- \
    /opt/java/bin/jstat -gcutil 1 1 1 | \
    awk 'NF >= 10 && $1 ~ /^[0-9.]+$/ { print int($9); exit }'
}

memory_snapshot() {
  local pod=$1 label=$2 gc_values heap_used old_used fgc rss
  gc_values=$(kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" exec "$pod" -c app -- \
    /opt/java/bin/jstat -gc 1 1 1 | \
    awk 'NF >= 19 && $1 ~ /^[0-9.]+$/ { printf "%.0f %.0f %d\n", $3+$4+$6+$8, $8, $15; exit }')
  read -r heap_used old_used fgc <<<"$gc_values"
  rss=$(kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" exec "$pod" -c app -- \
    /bin/awk '/VmRSS/ { print $2; exit }' /proc/1/status)
  jq -cn --arg target "$label:$pod" --argjson heap "$heap_used" --argjson old "$old_used" \
    --argjson rss "$rss" --argjson fgc "$fgc" \
    '{target:$target,heapUsedKiB:$heap,oldUsedKiB:$old,rssKiB:$rss,fullGcCount:$fgc}'
}

wait_for_fgc() {
  local pod=$1 expected=$2 timeout=$3 deadline current
  deadline=$((SECONDS + timeout))
  while ((SECONDS < deadline)); do
    current=$(fgc_count "$pod")
    if [[ -n $current ]] && ((current >= expected)); then
      printf '%s\n' "$current"
      return 0
    fi
    sleep 1
  done
  fail "Full GC count for $pod did not reach $expected; current=${current:-unknown}"
}

generate_load() {
  local url=$1 output=$2 request
  : >"$output"
  for ((request = 1; request <= LOAD_REQUESTS; request++)); do
    (curl -sS -o /dev/null -w '%{http_code} %{time_total}\n' "$url/query" || true) >>"$output" &
    if ((request % LOAD_CONCURRENCY == 0)); then
      wait
    fi
  done
  wait
}

create_full_gc_experiment() {
  local labels=$1 interval=$2 count=$3 timeout=$4 output status
  if output=$("$BLADE" create k8s container-jvm full-gc \
    --labels "$labels" --container-names app --namespace "$NAMESPACE" \
    --kubeconfig "$KUBECONFIG_PATH" --interval "$interval" --effect-count "$count" \
    --timeout "$timeout" --pid 1 --javaHome /opt/java --chaosblade-override 2>&1); then
    status=0
  else
    status=$?
  fi
  printf '%s\n' "$output" | tail -n 1
  return "$status"
}

assert_resource_has_targets() {
  local uid=$1 expected=$2 resource actual
  resource=$(kubectl --kubeconfig "$KUBECONFIG_PATH" get chaosblade "$uid" -o json)
  actual=$(jq '[.status.expStatuses[0].resStatuses[]?] | length' <<<"$resource")
  ((actual == expected)) || fail "ChaosBlade $uid has $actual target statuses, expected $expected"
  jq -e --argjson expected "$expected" '
    .status.phase == "Running"
    and ([.status.expStatuses[0].resStatuses[]?] | length) == $expected
    and all(.status.expStatuses[0].resStatuses[]?; .success == true and .state == "Success")
  ' >/dev/null <<<"$resource" || fail "ChaosBlade $uid target state is not Running/Success"
}

run_single_target() {
  local pod before expected after response uid load_file load_pid summary stable memory_before memory_after
  pod=$(pod_by_label app=datasource-hikari)
  before=$(fgc_count "$pod")
  memory_before=$(memory_snapshot "$pod" single-before)
  expected=$((before + 3))
  load_file=$(mktemp)
  LOAD_FILES+=("$load_file")
  generate_load "$HIKARI_BASE_URL" "$load_file" &
  load_pid=$!
  response=$(create_full_gc_experiment app=datasource-hikari 1000 3 30)
  assert_json "$response" '.success == true' "single-target Full GC create failed"
  uid=$(experiment_uid "$response")
  EXPERIMENT_UIDS+=("$uid")
  assert_resource_has_targets "$uid" 1
  after=$(wait_for_fgc "$pod" "$expected" 30)
  wait "$load_pid"
  summary=$(load_summary "$load_file" "single:$pod")
  "$BLADE" destroy "$uid" --target k8s --kubeconfig "$KUBECONFIG_PATH" >/dev/null
  sleep 3
  stable=$(fgc_count "$pod")
  ((stable <= after + 1)) || fail "Full GC continued after destroy on $pod: $after -> $stable"
  memory_after=$(memory_snapshot "$pod" single-after)
  echo "METRIC: $summary"
  echo "METRIC: $memory_before"
  echo "METRIC: $memory_after"
  echo "PASS: single-target Full GC count $before -> $after, stable after destroy at $stable"
}

run_cluster_targets() {
  local hikari druid before_hikari before_druid response uid after_hikari after_druid
  local hikari_load druid_load hikari_pid druid_pid
  local hikari_memory_before druid_memory_before hikari_memory_after druid_memory_after
  hikari=$(pod_by_label app=datasource-hikari)
  druid=$(pod_by_label app=datasource-druid)
  kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" label pod "$hikari" "$druid" \
    scenario=full-gc-cluster --overwrite >/dev/null
  before_hikari=$(fgc_count "$hikari")
  before_druid=$(fgc_count "$druid")
  hikari_memory_before=$(memory_snapshot "$hikari" cluster-before)
  druid_memory_before=$(memory_snapshot "$druid" cluster-before)
  hikari_load=$(mktemp)
  druid_load=$(mktemp)
  LOAD_FILES+=("$hikari_load" "$druid_load")
  generate_load "$HIKARI_BASE_URL" "$hikari_load" &
  hikari_pid=$!
  generate_load "$DRUID_BASE_URL" "$druid_load" &
  druid_pid=$!
  response=$(create_full_gc_experiment scenario=full-gc-cluster 1000 2 30)
  assert_json "$response" '.success == true' "cluster-target Full GC create failed"
  uid=$(experiment_uid "$response")
  EXPERIMENT_UIDS+=("$uid")
  assert_resource_has_targets "$uid" 2
  after_hikari=$(wait_for_fgc "$hikari" "$((before_hikari + 2))" 30)
  after_druid=$(wait_for_fgc "$druid" "$((before_druid + 2))" 30)
  hikari_memory_after=$(memory_snapshot "$hikari" cluster-after)
  druid_memory_after=$(memory_snapshot "$druid" cluster-after)
  wait "$hikari_pid" "$druid_pid"
  echo "METRIC: $(load_summary "$hikari_load" "cluster:$hikari")"
  echo "METRIC: $(load_summary "$druid_load" "cluster:$druid")"
  echo "METRIC: $hikari_memory_before"
  echo "METRIC: $hikari_memory_after"
  echo "METRIC: $druid_memory_before"
  echo "METRIC: $druid_memory_after"
  "$BLADE" destroy "$uid" --target k8s --kubeconfig "$KUBECONFIG_PATH" >/dev/null
  echo "PASS: cluster Full GC hikari $before_hikari -> $after_hikari, druid $before_druid -> $after_druid"
}

run_hard_ttl() {
  local pod before response uid active stopped final memory_before memory_after
  pod=$(pod_by_label app=datasource-druid)
  before=$(fgc_count "$pod")
  memory_before=$(memory_snapshot "$pod" ttl-before)
  response=$(create_full_gc_experiment app=datasource-druid 500 0 8)
  assert_json "$response" '.success == true' "Full GC TTL create failed"
  uid=$(experiment_uid "$response")
  EXPERIMENT_UIDS+=("$uid")
  active=$(wait_for_fgc "$pod" "$((before + 2))" 15)
  sleep 9
  stopped=$(fgc_count "$pod")
  sleep 3
  final=$(fgc_count "$pod")
  ((final <= stopped + 1)) || fail "Full GC continued after hard TTL on $pod: $stopped -> $final"
  memory_after=$(memory_snapshot "$pod" ttl-after)
  echo "METRIC: $memory_before"
  echo "METRIC: $memory_after"
  echo "PASS: hard TTL stopped Full GC on $pod; active=$active stopped=$stopped final=$final"
}

wait_for_http "$HIKARI_BASE_URL/query" 200 60
wait_for_http "$DRUID_BASE_URL/query" 200 60
run_single_target
run_cluster_targets
run_hard_ttl
echo "PASS: JVM Full GC E2E"
