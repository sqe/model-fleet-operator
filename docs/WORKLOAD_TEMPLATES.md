# Workload templates and model sources

The `examples` directory contains templates for the main workload paths.

`examples/inference-kind.yaml`
: CPU smoke test with Cilium routing for kind.

`examples/inference-vllm.yaml`
: Hugging Face model served by vLLM on a GPU cluster.

`examples/training-pytorch.yaml`
: GPU training with PVC-backed workspace and artifact storage, plus MLflow.

`examples/dataset-huggingface-public.yaml`, `examples/dataset-pvc.yaml`
: Immutable remote and PVC-backed Dataset registrations.

`examples/training-with-dataset.yaml`
: TrainingRun pinned to an exact Dataset version.

`examples/validation/gpu-node.yaml`
: GPU driver and memory validation.

`examples/validation/openai-api.yaml`
: OpenAI-compatible endpoint validation.

`examples/templates/inference-prefetched.yaml.tmpl`
: Approved model downloader and shared cache scaffold.

`examples/templates/inference-minio-prefetched.yaml.tmpl`
: MinIO or S3-compatible model prefetch into a shared pod volume.

`examples/templates/training-from-image.yaml.tmpl`
: Immutable training image scaffold for a GPU cluster.

Create the namespace before applying a workload:

```bash
kubectl create namespace models
kubectl apply -f examples/inference-vllm.yaml
kubectl get isvc,deploy,service,scaledobject,httproute -n models
```

## Hugging Face models

The vLLM template passes a Hugging Face repository ID to vLLM. vLLM downloads
the model when the pod starts. Every Hugging Face example expects `HF_TOKEN` in
the namespace-local `huggingface-read-token` Secret. This works for public,
private, and gated repositories without placing the token in the custom
resource.

```bash
read -s HF_TOKEN
kubectl -n models create secret generic huggingface-read-token \
  --from-literal=token="$HF_TOKEN" --dry-run=client -o yaml | kubectl apply -f -
unset HF_TOKEN
```

Rotate the Secret by rerunning this command, then restart or recreate pods that
need the new token.

Pin `spec.model.version` and the runtime's revision argument to a commit hash for
repeatable deployments. A branch such as `main` can change without producing a
new Kubernetes revision.

Large models should use one of these cache strategies:

* Let the serving runtime manage its cache on node-local storage for the fastest
  startup after the first download on that node.
* Mount a PVC at `HF_HOME` when the storage class supports the required access
  mode and throughput.
* Use `spec.initContainers` with an approved downloader image and a shared
  volume. The downloader image used by the template must expose `hf` as its
  entrypoint. The example under `examples/templates` shows this contract.

Model Fleet applies a restricted security context to these init containers.
Privileged execution, privilege escalation, and added Linux capabilities are
rejected during reconciliation.

The operator process does not download model bytes. It reconciles the init
container, volume, credentials reference, and serving container into the same
pod. This keeps large downloads and repository credentials out of the control
plane.

## Source repositories

Build training and inference code into an immutable OCI image in CI. Do not make
production pods clone a branch and execute it at startup. Building the image
first provides dependency scanning, an image digest, and a reviewable link from
source revision to running workload.

An init container remains available for controlled artifact downloads when a
model or dataset cannot be packaged into the image. Pin the artifact revision,
use a dedicated read-only credential, verify checksums when the source provides
them, and write to a mounted volume.

Prefer a [Dataset registration](DATASETS.md) for training inputs. It records an
exact version and can attach an existing PVC read-only. It does not execute the
URI or replace the workload's cloud identity.

## `model.uri` behavior

`spec.model.uri` records model provenance and is exported to the serving
container as `MODEL_URI`. It does not trigger a download by itself. The serving
container or an init container must understand the URI scheme. This avoids
assuming that `hf://`, `s3://`, `gs://`, and private registries share one
authentication or caching model.
