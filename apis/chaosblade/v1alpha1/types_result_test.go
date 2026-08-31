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
