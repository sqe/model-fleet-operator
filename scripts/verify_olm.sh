#!/usr/bin/env bash
# Requires an isolated Kubernetes context with OLM and the CSV already installed.
set -Eeuo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
VERSION=$(${PYTHON:-python3} -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
kubectl -n model-fleet-system wait --for=create "csv/model-fleet-operator.v$VERSION" --timeout=5m
kubectl -n model-fleet-system wait --for=jsonpath='{.status.phase}'=Succeeded "csv/model-fleet-operator.v$VERSION" --timeout=5m
kubectl -n model-fleet-system rollout status deployment/model-fleet-operator --timeout=3m
# Different namespace from the operator: validates the advertised AllNamespaces contract.
kubectl create namespace model-fleet-smoke --dry-run=client -o yaml | kubectl apply -f -
kubectl -n model-fleet-smoke apply -f "$ROOT/config/olm/examples.yaml"
kubectl -n model-fleet-smoke wait --for=create deployment/model-echo --timeout=3m
kubectl -n model-fleet-smoke rollout status deployment/model-echo --timeout=3m
kubectl -n model-fleet-smoke wait --for=jsonpath='{.status.phase}'=Ready inferenceservice/model-echo --timeout=3m
kubectl -n model-fleet-smoke wait --for=create job/cpu-smoke-test --timeout=3m
kubectl -n model-fleet-smoke wait --for=condition=complete job/cpu-smoke-test --timeout=5m
kubectl -n model-fleet-smoke wait --for=jsonpath='{.status.phase}'=Succeeded trainingrun/cpu-smoke-test --timeout=3m
kubectl -n model-fleet-smoke wait --for=jsonpath='{.status.phase}'=Registered dataset/example-v1 --timeout=3m
kubectl -n model-fleet-smoke wait --for=jsonpath='{.status.phase}'=Registered agentregistration/example-agent --timeout=3m
# Reconciliation must react to updated intent, not just create initial children.
kubectl -n model-fleet-smoke patch inferenceservice model-echo --type=merge -p '{"spec":{"suspend":true}}'
kubectl -n model-fleet-smoke wait --for=jsonpath='{.spec.replicas}'=0 deployment/model-echo --timeout=2m
kubectl -n model-fleet-smoke patch inferenceservice model-echo --type=merge -p '{"spec":{"suspend":false}}'
kubectl -n model-fleet-smoke wait --for=jsonpath='{.spec.replicas}'=1 deployment/model-echo --timeout=2m
kubectl -n model-fleet-smoke rollout status deployment/model-echo --timeout=3m
kubectl -n model-fleet-smoke wait --for=jsonpath='{.status.phase}'=Ready inferenceservice/model-echo --timeout=3m
# Exercise the generated Service and actual HTTP workload after resume.
kubectl -n model-fleet-smoke port-forward service/model-echo 18080:80 >/dev/null 2>&1 &
FORWARD_PID=$!
trap 'kill "$FORWARD_PID" 2>/dev/null || true' EXIT
curl --fail --silent --show-error --retry 15 --retry-connrefused --retry-delay 1 \
  --max-time 10 http://127.0.0.1:18080/olm-smoke \
  | "${PYTHON:-python3}" -c 'import json,sys; data=json.load(sys.stdin); assert data["http"]["originalUrl"] == "/olm-smoke"; assert data["environment"]["MODEL_NAME"] == "local-smoke-test"'
echo 'PASS: OLM controller, four CRDs, cross-namespace reconciliation, suspend/resume and HTTP response.'
