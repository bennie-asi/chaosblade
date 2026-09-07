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

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

json_value() {
  local json=$1
  local expression=$2
  jq -er "$expression" <<<"$json"
}

assert_json() {
  local json=$1
  local expression=$2
  local description=$3
  jq -e "$expression" >/dev/null <<<"$json" || fail "$description; response=$json"
}

http_status() {
  local url=$1
  curl -sS -o /tmp/chaosblade-datasource-response.json -w '%{http_code}' "$url"
}

wait_for_pool_active() {
  local url=$1
  local expected=$2
  local timeout_seconds=$3
  local deadline=$((SECONDS + timeout_seconds))
  while ((SECONDS < deadline)); do
    local pool
    if pool=$(curl -fsS "$url" 2>/dev/null) &&
        jq -e --argjson expected "$expected" '.active == $expected' >/dev/null <<<"$pool"; then
      echo "$pool"
      return 0
    fi
    sleep 1
  done
  fail "pool did not reach active=$expected within ${timeout_seconds}s: $url"
}

wait_for_http() {
  local url=$1
  local expected=$2
  local timeout_seconds=$3
  local deadline=$((SECONDS + timeout_seconds))
  while ((SECONDS < deadline)); do
    if [[ $(http_status "$url") == "$expected" ]]; then
      return 0
    fi
    sleep 1
  done
  fail "HTTP endpoint did not reach status $expected within ${timeout_seconds}s: $url"
}
