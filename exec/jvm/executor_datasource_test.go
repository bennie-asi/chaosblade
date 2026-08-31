package jvm

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/chaosblade-io/chaosblade-spec-go/spec"
)

func TestCreateURLForwardsTimeoutOnlyForDatasource(t *testing.T) {
	tests := []struct {
		name        string
		model       *spec.ExpModel
		wantTimeout bool
	}{
		{
			name: "datasource connection pool",
			model: &spec.ExpModel{
				Target:      "datasource",
				ActionName:  "connectionpoolfull",
				ActionFlags: map[string]string{"timeout": "60"},
			},
			wantTimeout: true,
		},
		{
			name: "legacy jvm action",
			model: &spec.ExpModel{
				Target:      "jvm",
				ActionName:  "full-gc",
				ActionFlags: map[string]string{"timeout": "60"},
			},
			wantTimeout: false,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			ctx := context.WithValue(context.Background(), spec.Uid, "test-uid")
			_, body, response := NewExecutor().createUrl(ctx, "12345", test.model)
			if response != nil {
				t.Fatalf("createUrl failed: %s", response.Print())
			}
			var request map[string]string
			if err := json.Unmarshal(body, &request); err != nil {
				t.Fatal(err)
			}
			_, present := request["timeout"]
			if present != test.wantTimeout {
				t.Fatalf("timeout presence=%t, want %t; request=%v", present, test.wantTimeout, request)
			}
		})
	}
}
