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

package v1alpha1

import (
	"encoding/json"
	"testing"
)

func TestResourceStatusResultRoundTripAndDeepCopy(t *testing.T) {
	original := &ChaosBlade{
		Status: ChaosBladeStatus{ExpStatuses: []ExperimentStatus{{
			ResStatuses: []ResourceStatus{{
				Result: json.RawMessage(`{"state":"ACTIVE","actualHold":4}`),
			}},
		}}},
	}
	encoded, err := json.Marshal(original)
	if err != nil {
		t.Fatal(err)
	}
	var decoded ChaosBlade
	if err := json.Unmarshal(encoded, &decoded); err != nil {
		t.Fatal(err)
	}
	if got := string(decoded.Status.ExpStatuses[0].ResStatuses[0].Result); got != `{"state":"ACTIVE","actualHold":4}` {
		t.Fatalf("unexpected result: %s", got)
	}
	copy := original.DeepCopy()
	copy.Status.ExpStatuses[0].ResStatuses[0].Result[10] = 'R'
	if string(copy.Status.ExpStatuses[0].ResStatuses[0].Result) == string(original.Status.ExpStatuses[0].ResStatuses[0].Result) {
		t.Fatal("deep copy shares result bytes")
	}
}
