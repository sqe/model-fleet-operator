# GPU and Apple Silicon inference

Model Fleet uses two honest execution paths. Kubernetes schedules containerized
inference onto GPUs advertised through device plugins. Apple MLX uses Metal and
must run natively on macOS; a kind node is a Linux container and cannot access
the Mac GPU. The allowlisted Model Fleet gateway gives both paths one
OpenAI-compatible API and one telemetry contract.

```{mermaid}
flowchart LR
  Client[Agent or application] --> Gateway[Model Fleet gateway]
  Gateway --> Choice{Allowlisted route}
  Choice -->|runtime=vllm| Service[InferenceService]
  Service --> Scheduler[Kubernetes scheduler]
  Scheduler -->|nvidia.com/gpu| GPU[NVIDIA GPU node]
  Choice -->|runtime=mlx| Host[mlx-lm on Apple Silicon host]
  GPU --> Metrics[Prometheus and Grafana]
  Host -->|tokens, requests, errors, latency| Metrics
```

## Kubernetes GPU path

Declare tested module budgets under `spec.accelerator.fit`. Model Fleet computes
the per-device requirement, selects the smallest portable GPU class, requests
`nvidia.com/gpu`, and optionally constrains NVIDIA product labels. It does not
guess model size or inspect live free VRAM.

```bash
kubectl get nodes \
  -L model-fleet.sqe.io/gpu-class,nvidia.com/gpu.product
kubectl apply -f examples/inference-vllm.yaml
kubectl get isvc,deploy,pod -n models
```

Use [`inference-multi-gpu-fit.yaml`](../examples/inference-multi-gpu-fit.yaml)
for sharded modules and [`inference-qwen38-27b-24gb-offload.yaml`](../examples/inference-qwen38-27b-24gb-offload.yaml)
for a constrained 24 GiB experiment. NVIDIA GPU Operator or the cloud image,
not Model Fleet, owns drivers and the device plugin.

## Native MLX path for kind on macOS

Install MLX in a dedicated Python 3.12 environment and start the guarded helper:

```bash
python3.12 -m venv .venv-mlx
.venv-mlx/bin/pip install --upgrade mlx-lm
bash scripts/run_mlx_inference.sh
```

The helper requires Apple Silicon, detects `mlx_lm.server`, checks free disk,
warns when unified memory is below 24 GiB, and defaults to
`mlx-community/Qwen3.8-27B-4bit`. Override it without changing code:

```bash
MLX_MODEL=mlx-community/Qwen3-14B-4bit \
MLX_PORT=8081 bash scripts/run_mlx_inference.sh
```

Docker Desktop exposes the host to kind as `host.docker.internal`. Confirm this
before installing the route:

```bash
kubectl run mlx-connectivity --rm -i --restart=Never \
  --image=curlimages/curl -- \
  curl -fsS http://host.docker.internal:8081/v1/models

kubectl -n model-fleet-system create secret generic model-fleet-control \
  --from-literal=control-plane-api-key="$(openssl rand -hex 32)" \
  --dry-run=client -o yaml | kubectl apply -f -

helm upgrade --install model-fleet charts/model-fleet-operator \
  -n model-fleet-system --create-namespace \
  -f examples/mlx-kind-values.yaml
```

Call the authenticated Model Fleet gateway, not the host server directly:

```bash
kubectl -n model-fleet-system port-forward svc/model-fleet-gateway 8000:8000
TOKEN=$(kubectl -n model-fleet-system get secret model-fleet-control \
  -o jsonpath='{.data.control-plane-api-key}' | base64 --decode)
curl -fsS http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen38-mlx","messages":[{"role":"user","content":"Summarize the fleet."}]}'
```

`host.docker.internal` is a local Docker Desktop facility, not a production
endpoint. For shared environments, run a supported container runtime on GPU
nodes or expose a separately secured inference endpoint over controlled
networking. Never advertise a host MLX process as an operator-managed
`InferenceService` because Kubernetes cannot reconcile or restart that process.

## Visual evidence

The Grafana **Gateway runtime traffic and latency** panel groups request rate,
errors, and p95 latency by `model` and `runtime`, so `mlx` and `vllm` are visible
on the same graph. Token and cost panels use the same route labels. Kubernetes
GPU utilization, framebuffer, power, and cost continue to come from DCGM and
OpenCost; MLX host GPU utilization is not fabricated from unavailable cluster
metrics.
