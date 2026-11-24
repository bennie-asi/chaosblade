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
	"github.com/chaosblade-io/chaosblade-spec-go/spec"
	"testing"
)

func TestMatcherDestroyUID(t *testing.T) {
	for _, tc := range []struct{ scope, protocol, action, uid, want string }{
		{"cri", "tcp,udp", "loss", "child", "child"}, {"cri", "udp,icmp", "delay", "child", "child"},
		{"cri", "tcp,icmp", "reorder", "child", "child"}, {"cri", "tcp,udp", "loss", "", spec.UnknownUid},
		{"cri", "tcp", "loss", "child", spec.UnknownUid}, {"host", "tcp,udp", "loss", "child", spec.UnknownUid},
		{"cri", "tcp,udp", "corrupt", "child", spec.UnknownUid},
	} {
		m := &spec.ExpModel{Scope: tc.scope, Target: "network", ActionName: tc.action, ActionFlags: map[string]string{"uid": tc.uid, "protocol": tc.protocol}}
		if v := matcherDestroyUID(m); v != tc.want {
			t.Fatalf("%+v got %q", tc, v)
		}
	}
}
