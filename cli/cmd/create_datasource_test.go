/*
 * Copyright 2025 The ChaosBlade Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package cmd

import (
	"encoding/json"
	"testing"

	"github.com/chaosblade-io/chaosblade-spec-go/spec"
)

func TestCreateResultPreservesLegacyUID(t *testing.T) {
	model := &spec.ExpModel{Target: "jvm", ActionName: "full-gc"}
	if got := createResult("legacy-id", model, "ignored"); got != "legacy-id" {
		t.Fatalf("expected legacy uid, got %#v", got)
	}
}

func TestCreateResultWrapsDatasourceDetail(t *testing.T) {
	model := &spec.ExpModel{Target: "datasource", ActionName: "connectionpoolfull"}
	got := createResult("pool-id", model, `{"state":"ACTIVE","actualHold":4}`)
	value, err := json.Marshal(got)
	if err != nil {
		t.Fatal(err)
	}
	if string(value) != `{"uid":"pool-id","detail":{"state":"ACTIVE","actualHold":4}}` {
		t.Fatalf("unexpected envelope: %s", value)
	}
}
