# JVM datasource connection-pool E2E

This fixture starts Spring Boot 2.7 applications backed by a four-connection HikariCP or Druid pool. The application exposes only safe diagnostics: `/query` executes a local H2 query and `/pool` reports pool type, maximum, active, and idle counts.

On a disposable Linux k3s node with Docker, Maven, Go, JDK 17, Helm, `jq`, and `busybox-static`:

```bash
export KUBECONFIG_PATH=/etc/rancher/k3s/k3s.yaml
export BLADE_PACKAGE=/absolute/path/to/chaosblade-1.8.0-linux_amd64
./build-and-deploy.sh
export BLADE_HOME=$BLADE_PACKAGE
./run-e2e.sh
```

The runner verifies Hikari explicit destroy, Druid hard-TTL release, and action-specific rollback when one of two selected Pods cannot accept the injection. It fails unless the structured child result, business impact, and recovery gates all pass. `./run-e2e.sh --self-test` validates the shell and JSON gates without a cluster.
