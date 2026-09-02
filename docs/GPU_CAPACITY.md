# GPU fit and portable capacity

Model Fleet selects GPU nodes from declared workload requirements. It never
guesses memory from a model name or downloads model artifacts into the operator.
The author can provide a tested per-device floor, a module-level fit plan, or
both.

## Module-level fit selector

Use `fit.modules` when weights, cache, adapters, optimizer state, and runtime
overhead have separate memory budgets:

```yaml
spec:
  accelerator:
    count: 2
    fit:
      modules:
        - {name: model-weights, memoryGiB: 70, distribution: sharded}
        - {name: kv-cache, memoryGiB: 20, distribution: sharded}
        - {name: runtime, memoryGiB: 4, distribution: replicated}
      safetyMarginPercent: 10
    products:
      - NVIDIA-A100-SXM4-80GB
      - NVIDIA-H100-80GB-HBM3
```

`sharded` memory is divided by `count`; `replicated` memory is needed on every
device. The required memory per GPU is:

```text
ceil((sum(sharded) / GPU count + sum(replicated)) × (1 + safety margin / 100))
```

The example requires `ceil((90 / 2 + 4) × 1.10) = 54 GiB` per GPU. The operator
therefore selects `gpu-80gb`, requests two `nvidia.com/gpu` devices, and adds
required node affinity for either listed NVIDIA GPU product. The calculated
value is recorded on the pod template as
`fleet.sqe.io/gpu-memory-per-device-gib=54`.

`minimumMemoryGiB` remains useful for measured workloads. If it is supplied
with `fit`, the larger of the measured floor and calculated fit is used. An
explicit class that cannot hold the result is rejected before a pod is created.

## Portable classes

| Required memory per GPU | Node label |
|---|---|
| ≤24 GiB | `model-fleet.sqe.io/gpu-class=gpu-24gb` |
| ≤48 GiB | `model-fleet.sqe.io/gpu-class=gpu-48gb` |
| ≤80 GiB | `model-fleet.sqe.io/gpu-class=gpu-80gb` |
| >80 GiB | `model-fleet.sqe.io/gpu-class=gpu-80gb-plus` |

On AWS, the included Karpenter capacity chart turns these labels into NodePools
constrained by Karpenter's GPU-memory labels. On GKE, apply the same class label
to autoscaled GPU node pools. On MicroK8s or bare metal, label physical nodes
after checking their usable memory. `products` relies on the standard
`nvidia.com/gpu.product` label published by NVIDIA GPU Feature Discovery; use
the exact values reported by `kubectl get nodes -L nvidia.com/gpu.product`.

Product affinity must also be visible to a capacity provider while scaling from
zero. For Karpenter, use product-specific NodePools whose template carries the
same `nvidia.com/gpu.product` label and whose AWS requirements constrain the
corresponding `karpenter.k8s.aws/instance-gpu-name`. The included generic class
NodePools intentionally advertise memory classes only, so omit `products` when
using them unchanged. GKE and bare-metal pools likewise need the product label
on their node-pool template, not only a label added after the node starts.

## What fit does and does not prove

The selector is deterministic admission and scheduling intent, not live VRAM
bin-packing. Kubernetes allocates whole GPU devices and does not expose current
free VRAM to its scheduler. The declared budgets must include the runtime's real
weights, quantization, context/KV cache, CUDA graphs, adapters, optimizer state,
and fragmentation behavior. Validate those numbers under representative load,
then increase the margin or `minimumMemoryGiB` if observed peaks are higher.

GPU software remains an infrastructure responsibility. Cloud images or NVIDIA
GPU Operator own drivers and the device plugin. Model Fleet never launches a
privileged installer from a workload custom resource.

See [`examples/inference-multi-gpu-fit.yaml`](../examples/inference-multi-gpu-fit.yaml)
for a complete two-GPU service. For a Pending pod, follow the capacity decision
procedure in [Operator runbooks](RUNBOOKS.md).
