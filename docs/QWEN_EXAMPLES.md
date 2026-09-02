# Qwen gateway, inference, and vision training

An OpenAI-compatible model endpoint can serve two roles at once: application
inference and the supervisor's constrained skill selection. Configure the Model
Fleet supervisor route to the `InferenceService` ClusterIP and keep the model
endpoint private. The supervisor validates the returned skill against active
Agent Cards; the model is not an authorization boundary.

## Qwen3.8-27B on a 24 GiB GPU

[`inference-qwen38-27b-24gb-offload.yaml`](../examples/inference-qwen38-27b-24gb-offload.yaml)
is an explicit **offload experiment**, not a claim that the model fits entirely
in 24 GiB. The referenced community AWQ-INT4 artifact reports a 21.02 GB model
size, leaving too little space for KV cache, CUDA/runtime allocations, and
fragmentation. The official vLLM recipe reports that even NVFP4 requires 24.6
GiB. A 24 GiB card therefore does not hold this 27B model “plentifully.”

The example bounds context and concurrency, disables CUDA graphs, uses FP8 KV
cache, and offloads up to 6 GiB to host RAM. Offload reduces throughput and
still requires measurement on the exact GPU, driver, vLLM build, and prompt
mix. Validate startup and peak memory before changing its declared module
budgets. Use a 32/48 GiB GPU for a production 27B endpoint, or an official
smaller AWQ model such as `Qwen/Qwen3-14B-AWQ` when 24 GiB headroom matters.

Point a supervisor route at the service:

```json
{
  "router": {
    "base_url": "http://qwen38-27b-router.models.svc:80",
    "upstream_model": "qwen38-router",
    "namespace": "models",
    "workload": "qwen38-27b-router"
  }
}
```

## Qwen3-VL image LoRA

[`training-qwen3-vl-images.yaml`](../examples/training-qwen3-vl-images.yaml)
runs the dedicated `images/qwen3-vl-training` container from the companion
agentic platform. Build and publish that image under an immutable digest, then
replace the example image reference. The container pins the official Qwen3-VL
training source and defaults to `Qwen/Qwen3-VL-4B-Instruct`, LoRA, BF16, Flash
Attention, gradient checkpointing, and one GPU.

The manifest uses `/models/qwen3-vl-4b`: hydrate that directory from a versioned
S3/RustFS snapshot of Hugging Face revision
`ebb281ec70b05090aa6165b016eac8ec08e71b17` before the Job starts. This avoids
mutable upstream downloads on every retry.

The mounted `/data/annotations.json` must be JSON or JSONL in the official
format. Every `image` path is relative to `/data/images`, every image has one
matching `<image>` token in the human turn, and media tokens never appear in an
assistant answer:

```json
{
  "image": "sample.jpg",
  "conversations": [
    {"from": "human", "value": "<image>\nDescribe the instrument."},
    {"from": "gpt", "value": "A segmented infrared telescope mirror."}
  ]
}
```

Validate data rights, malformed images, held-out quality, and actual VRAM before
a long run. Store datasets, model snapshots, and output adapters in versioned
S3/RustFS prefixes; hydrate the PVC with an approved init container so retries
do not repeatedly download external artifacts.

Sources: [official Qwen3-VL fine-tuning framework](https://github.com/QwenLM/Qwen3-VL/tree/main/qwen-vl-finetune),
[official vLLM Qwen3.8 recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B), and
[the referenced AWQ model card](https://huggingface.co/cyankiwi/Qwen3.8-27B-AWQ-INT4).
