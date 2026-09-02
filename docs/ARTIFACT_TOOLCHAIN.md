# Model images and artifact services

Model Fleet keeps three different concerns separate:

| Service | Purpose | Development endpoint |
|---|---|---|
| OCI Distribution | Stores model-serving and training container images. | `registry.model-artifacts.svc:5000` |
| MinIO | Stores model weights, datasets, checkpoints, and run artifacts. | `http://minio.model-artifacts.svc:9000` |
| MLflow | Tracks experiments and model-registry metadata; artifacts go to MinIO. | `http://mlflow.model-artifacts.svc:5000` |

This separation lets the operator schedule containers without embedding large
weights in every image. It also keeps model lineage in MLflow while object bytes
remain in object storage.

## Build for amd64 and arm64

Authenticate to the target registry with its normal credential helper first.
The script never accepts or prints a registry password.

```bash
docker login registry.example.com
scripts/build_model_image.sh \
  --context ./model \
  --image registry.example.com/models/embed:v1 \
  --push
```

The default platform list is `linux/amd64,linux/arm64`. Every base image and
native dependency in the supplied Dockerfile must support both architectures.
The registry receives one OCI image index and one architecture-specific
manifest per platform. Inspect it with:

```bash
docker buildx imagetools inspect registry.example.com/models/embed:v1
```

Export the same multi-platform result as an OCI archive instead of publishing:

```bash
scripts/build_model_image.sh \
  --context ./model --image models/embed:v1 \
  --oci dist/embed-v1.oci.tar
```

Docker's local image store can load only one selected platform at a time:

```bash
scripts/build_model_image.sh \
  --context ./model --image models/embed:v1 \
  --platforms linux/arm64 --load
```

The equivalent Make target exports an OCI archive by default. Set
`MODEL_OUTPUT=--push` explicitly to publish.

```bash
make model-image MODEL_CONTEXT=./model MODEL_IMAGE=registry.example.com/models/embed:v1
```

## Review and deploy an image

The deployment helper emits a Model Fleet `InferenceService` as structured JSON.
It prints a dry run unless `--apply` is present.

```bash
scripts/deploy_inference_service.py \
  --image registry.example.com/models/embed:v1 \
  --model-name embed --model-version v1 \
  --model-uri s3://models/embed/v1/ \
  --namespace models --name embed-v1 --port 8080 \
  > /tmp/embed-v1.json

kubectl apply --dry-run=server -f /tmp/embed-v1.json
kubectl diff -f /tmp/embed-v1.json
kubectl apply -f /tmp/embed-v1.json
```

Or let the helper apply only after reviewing its generated form:

```bash
scripts/deploy_inference_service.py \
  --image registry.example.com/models/embed:v1 \
  --model-name embed --model-version v1 \
  --model-uri s3://models/embed/v1/ \
  --namespace models --name embed-v1 --port 8080 --apply
```

Add `--image-pull-secret <name>` for a private registry. Add
`--gateway-host embed.example.com --gateway-section https` for a Cilium
Gateway API route. The operator creates and keeps the Deployment, Service,
ScaledObject, and HTTPRoute aligned with the custom resource.

## Start the development artifact stack

The included stack is useful for kind, MicroK8s, and integration testing:

```bash
make artifacts-install
make artifacts-status
```

Installation generates MinIO credentials, creates the `models`, `datasets`, and
`mlflow` buckets, and waits for initialization. Store the printed credentials in
a local secret manager. To give a workload access, copy a narrowly scoped MinIO
user into a Secret in that workload's namespace rather than sharing the root
credential. The template at
`examples/templates/inference-minio-prefetched.yaml.tmpl` shows the expected
Secret keys and prefetch contract.

All services are ClusterIP-only. Inspect them from the workstation with three
separate terminals:

```bash
kubectl -n model-artifacts port-forward service/registry 5000:5000
kubectl -n model-artifacts port-forward service/minio 9000:9000 9001:9001
kubectl -n model-artifacts port-forward service/mlflow 5001:5000
```

Then open the MinIO console at `http://127.0.0.1:9001`, MLflow at
`http://127.0.0.1:5001`, or inspect the OCI catalog:

```bash
curl http://127.0.0.1:5000/v2/_catalog
```

Docker accepts an unauthenticated localhost registry for local experiments. A
Kubernetes node will not automatically trust the stack's plain-HTTP service.
Configure the node runtime explicitly for development, or use an authenticated
TLS registry such as ECR, Artifact Registry, GHCR, or a production Distribution
deployment for workloads that must pull images.

## Retention and production boundary

```bash
make artifacts-uninstall
```

Uninstall removes workloads, Services, the initialization Job, and credentials,
but retains PVCs and the namespace. `CONFIRM_PURGE=yes
infra/artifacts/artifacts.sh purge` removes the namespace and is intentionally
not a Make target. Whether volumes survive purge depends on the StorageClass
reclaim policy.

The stack is a single-replica development reference. Production needs TLS,
authentication and authorization, image scanning, network policy, backups,
highly available object storage, and an external MLflow database. Registry and
MinIO images support amd64 and arm64. The official MLflow image's arm64 support
is not guaranteed here; publish a tested internal multi-platform MLflow image
before using this stack on arm64 nodes.
