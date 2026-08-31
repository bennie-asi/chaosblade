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
