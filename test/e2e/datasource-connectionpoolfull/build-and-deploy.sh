#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd "$SCRIPT_DIR/../../.." && pwd)
JVM_REPO=${JVM_REPO:-$(cd "$ROOT_DIR/../chaosblade-exec-jvm" && pwd)}
OPERATOR_REPO=${OPERATOR_REPO:-$(cd "$ROOT_DIR/../chaosblade-operator" && pwd)}
KUBECONFIG_PATH=${KUBECONFIG_PATH:-/etc/rancher/k3s/k3s.yaml}
NAMESPACE=${NAMESPACE:-chaosblade-e2e}
BLADE_VERSION=${BLADE_VERSION:-1.8.0}
APP_IMAGE=${APP_IMAGE:-chaosblade-e2e/datasource-pool:local}
TOOL_IMAGE=${TOOL_IMAGE:-chaosblade-e2e/chaosblade-tool:local}
OPERATOR_IMAGE=${OPERATOR_IMAGE:-chaosblade-e2e/chaosblade-operator:local}

# shellcheck disable=SC1091
# lib.sh is resolved next to this script at runtime.
source "$SCRIPT_DIR/lib.sh"
for command in git go make mvn docker kubectl helm jq readlink; do
  require_command "$command"
done
test -r "$KUBECONFIG_PATH" || fail "kubeconfig is not readable: $KUBECONFIG_PATH"

echo "root_sha=$(git -C "$ROOT_DIR" rev-parse HEAD)"
echo "jvm_sha=$(git -C "$JVM_REPO" rev-parse HEAD)"
echo "operator_sha=$(git -C "$OPERATOR_REPO" rev-parse HEAD)"

mvn -q -f "$SCRIPT_DIR/pom.xml" test package
make -C "$JVM_REPO" build

if [[ -z ${BLADE_PACKAGE:-} ]]; then
  JVM_BRANCH=$(git -C "$JVM_REPO" branch --show-current)
  OPERATOR_BRANCH=$(git -C "$OPERATOR_REPO" branch --show-current)
  make -C "$ROOT_DIR" linux_amd64 \
    BLADE_EXEC_JVM_PROJECT="file://$JVM_REPO" \
    BLADE_EXEC_JVM_BRANCH="$JVM_BRANCH" \
    BLADE_OPERATOR_PROJECT="file://$OPERATOR_REPO" \
    BLADE_OPERATOR_BRANCH="$OPERATOR_BRANCH"
  BLADE_PACKAGE=$(find "$ROOT_DIR/target" -maxdepth 1 -type d -name "chaosblade-*linux_amd64" -print -quit)
fi
test -x "$BLADE_PACKAGE/blade" || fail "BLADE_PACKAGE must point to an unpacked chaosblade distribution"

make -C "$OPERATOR_REPO" pre_build operator \
  BLADE_VERSION="$BLADE_VERSION" GOOS=linux GOARCH=amd64
OPERATOR_BINARY="$OPERATOR_REPO/build/_output/bin/chaosblade-operator"
test -x "$OPERATOR_BINARY" || fail "operator binary was not built"

IMAGE_ROOT="$SCRIPT_DIR/image-root"
JAVA_BIN=$(readlink -f "$(command -v java)")
JAVA_HOME_DIR=$(dirname "$(dirname "$JAVA_BIN")")
test -x /usr/bin/busybox || fail "install busybox-static before building the scratch app image"
rm -rf "$IMAGE_ROOT/java" "$IMAGE_ROOT/glibc" "$IMAGE_ROOT/java-config" "$IMAGE_ROOT/java-certs"
install -m 0755 /usr/bin/busybox "$IMAGE_ROOT/busybox"
install -m 0755 /usr/bin/cksum "$IMAGE_ROOT/cksum"
cp -a "$JAVA_HOME_DIR" "$IMAGE_ROOT/java"
cp -a /usr/lib/x86_64-linux-gnu "$IMAGE_ROOT/glibc"
install -m 0755 /lib64/ld-linux-x86-64.so.2 "$IMAGE_ROOT/ld-linux-x86-64.so.2"
cp -a /etc/java-17-openjdk "$IMAGE_ROOT/java-config"
cp -a /etc/ssl/certs/java "$IMAGE_ROOT/java-certs"
docker build -t "$APP_IMAGE" "$SCRIPT_DIR"

BUILD_CONTEXT=$(mktemp -d)
cleanup_context() { rm -rf "$BUILD_CONTEXT"; }
trap cleanup_context EXIT
cp "$SCRIPT_DIR/Dockerfile.tool" "$BUILD_CONTEXT/Dockerfile"
cp /usr/bin/busybox "$BUILD_CONTEXT/busybox"
cp -a "$BLADE_PACKAGE" "$BUILD_CONTEXT/chaosblade"
docker build -t "$TOOL_IMAGE" "$BUILD_CONTEXT"

rm -rf "${BUILD_CONTEXT:?}"/*
cp "$SCRIPT_DIR/Dockerfile.operator" "$BUILD_CONTEXT/Dockerfile"
cp /etc/ssl/certs/ca-certificates.crt "$BUILD_CONTEXT/ca-certificates.crt"
cp "$OPERATOR_BINARY" "$BUILD_CONTEXT/chaosblade-operator"
cp -a "$BLADE_PACKAGE" "$BUILD_CONTEXT/chaosblade"
docker build -t "$OPERATOR_IMAGE" "$BUILD_CONTEXT"

for image in "$APP_IMAGE" "$TOOL_IMAGE" "$OPERATOR_IMAGE"; do
  archive=$(mktemp --suffix=.tar)
  docker save "$image" -o "$archive"
  k3s ctr images import "$archive" >/dev/null
  rm -f "$archive"
done

kubectl --kubeconfig "$KUBECONFIG_PATH" create namespace "$NAMESPACE" \
  --dry-run=client -o yaml | kubectl --kubeconfig "$KUBECONFIG_PATH" apply -f -
if kubectl --kubeconfig "$KUBECONFIG_PATH" get crd chaosblades.chaosblade.io >/dev/null 2>&1; then
  echo "Reusing existing chaosblades.chaosblade.io CRD"
else
  kubectl --kubeconfig "$KUBECONFIG_PATH" apply \
    -f "$OPERATOR_REPO/deploy/crds/chaosblade.io_chaosblades_crd.yaml"
fi
KUBECONFIG="$KUBECONFIG_PATH" helm upgrade --install chaosblade-e2e \
  "$OPERATOR_REPO/deploy/helm/chaosblade-operator" -n "$NAMESPACE" \
  --set operator.repository="${OPERATOR_IMAGE%:*}" \
  --set operator.version="${OPERATOR_IMAGE##*:}" \
  --set operator.pullPolicy=Never \
  --set blade.repository="${TOOL_IMAGE%:*}" \
  --set blade.version="${TOOL_IMAGE##*:}" \
  --set blade.pullPolicy=Never \
  --set webhook.enable=false
kubectl --kubeconfig "$KUBECONFIG_PATH" apply -f "$SCRIPT_DIR/k8s-app.yaml"
kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" \
  rollout restart deployment/chaosblade-operator daemonset/chaosblade-tool
kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" \
  rollout restart deployment/datasource-hikari deployment/datasource-druid
kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" \
  rollout status deployment/chaosblade-operator --timeout=180s
kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" \
  rollout status daemonset/chaosblade-tool --timeout=180s
kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" \
  rollout status deployment/datasource-hikari --timeout=180s
kubectl --kubeconfig "$KUBECONFIG_PATH" -n "$NAMESPACE" \
  rollout status deployment/datasource-druid --timeout=180s
echo "PASS: images built, imported, and workloads deployed"
