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
	"testing"

	"github.com/chaosblade-io/chaosblade-spec-go/spec"
)

func TestMatcherDestroyUID(t *testing.T) {
	tests := []struct {
		name, scope, protocol, action, uid, want string
	}{
		{"cri loss", "cri", "tcp,udp", "loss", "child", "child"},
		{"cri delay", "cri", "udp,icmp", "delay", "child", "child"},
		{"cri reorder", "cri", "tcp,icmp", "reorder", "child", "child"},
		{"missing child uid", "cri", "tcp,udp", "loss", "", spec.UnknownUid},
		{"single protocol", "cri", "tcp", "loss", "child", spec.UnknownUid},
		{"host scope", "host", "tcp,udp", "loss", "child", spec.UnknownUid},
		{"unsupported action", "cri", "tcp,udp", "corrupt", "child", spec.UnknownUid},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			model := &spec.ExpModel{
				Scope:      tt.scope,
				Target:     "network",
				ActionName: tt.action,
				ActionFlags: map[string]string{
					"uid":      tt.uid,
					"protocol": tt.protocol,
				},
			}
			if got := matcherDestroyUID(model); got != tt.want {
				t.Errorf("matcherDestroyUID() = %q, want %q", got, tt.want)
			}
		})
	}
}
