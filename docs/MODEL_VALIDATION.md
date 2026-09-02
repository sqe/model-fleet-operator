# Model validation workflows

Validation uses `TrainingRun` Jobs, so the same workflow runs on kind, MicroK8s,
EKS, and GKE without another workflow controller.

- `examples/validation/openai-api.yaml` checks model discovery and one
  OpenAI-compatible chat completion, then prints latency and token usage as
  structured JSON. Set `TARGET_URL`, `MODEL`, and an optional secret-backed
  `API_KEY`.
- `examples/validation/gpu-node.yaml` proves the selected node has a working
  driver, container runtime, device plugin, and at least 24 GB of GPU memory.

```sh
kubectl apply -f examples/validation/gpu-node.yaml
kubectl apply -f examples/validation/openai-api.yaml
kubectl -n models logs job/validate-gpu-24gb
kubectl -n models logs job/validate-granite-api
kubectl -n models get trun
```

Copy these manifests per model and pin production image digests. A passing API
check does not establish model quality. Add domain-specific evaluation prompts,
evaluate their outputs in the container, and publish aggregate scores to MLflow.
Do not put prompts containing customer data into CR specs or pod logs.
