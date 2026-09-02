# Project value

Model Fleet gives platform teams one Kubernetes API for model inference,
training, validation, and operational control. It focuses on the control-plane
work that is otherwise repeated for every model deployment.

The practical value is not another proprietary model runtime. Teams keep vLLM,
PyTorch, Hugging Face, or their approved container, while Model Fleet replaces
one-off deployment scripts and custom training orchestration with standard
Kubernetes Deployments, Jobs, Services, Gateway API routes, KEDA scalers, OCI
images, and object storage. The same contract can serve a single local model or
many independently scaled services and runs across a multi-node cluster.

## What it provides

* **Consistent workload and dataset contracts.** Researchers declare a model,
  container, resources, scaling policy, route, and exact registered dataset
  versions without writing every generated Kubernetes resource.
* **Independent pod and node scaling.** KEDA controls replicas. Karpenter, GKE
  autoscaling, or a bare-metal machine provider controls capacity.
* **Portable GPU placement.** Workloads request a minimum GPU memory class. Each
  platform maps that class to its available hardware.
* **Cost controls.** Scale-to-zero, deadlines, retry limits, GPU utilization,
  model spend, token volume, and OpenCost rates are available through Grafana
  and Slack. Itemized cloud billing exports and measured or estimated
  bare-metal electricity remain separate from overlapping OpenCost estimates.
* **Repeatable model operations.** Versioned images, model revisions, validation
  Jobs, MLflow metadata, and durable Kafka commands provide an auditable path
  from experiment to service.
* **Built-in artifact path.** Buildx tooling produces amd64/arm64 image indexes,
  the deployment helper emits the Model Fleet API, and a development stack
  provides OCI Distribution, MinIO, and MLflow for local evaluation.
* **Clear security boundaries.** The operator reconciles Kubernetes resources
  but does not hold model registry or cloud storage credentials. Workloads use
  dedicated service accounts and Secret references.
* **One operational view.** Prometheus, DCGM, Hubble, KEDA, Kafka, and OpenCost
  metrics are collected in one dashboard. Slack can return status, cost reports,
  dashboard snapshots, and start or stop inference and managed agent runtimes.
* **Local-to-cloud development.** The same custom resources work in the provided
  kind environment, MicroK8s, EKS, and GKE.

## What it does not replace

Model Fleet does not replace an enterprise container build system, durable
production registry, object store, Kubernetes distribution, or cloud capacity
provider. The included artifact stack is a local reference, while production
systems keep their existing ownership and security controls. Model Fleet
coordinates them through standard Kubernetes resources.

Dataset registration is catalog metadata, not storage validation. It does not
grant cloud IAM, reserve provider capacity, or execute source repositories.
