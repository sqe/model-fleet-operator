Researcher and model developer guide
====================================

Container contract
------------------

Package inference or training code as a non-root OCI image. An inference image
must listen on ``spec.container.port`` and should expose readiness promptly.
Training images must checkpoint to durable storage before termination. Do not
store model weights or experiment output in a container filesystem.

The built-in Buildx wrapper produces an OCI image index for both common node
architectures, so the scheduler can place the same model version on amd64 or
arm64 nodes when the image's base and dependencies support both:

.. code-block:: bash

   make model-image MODEL_CONTEXT=./model MODEL_IMAGE=registry.example.com/models/demo:v1 \
     MODEL_OUTPUT=--push

See :doc:`ARTIFACT_TOOLCHAIN` for OCI tar export, registry login, dry-run
deployment, and the development registry, object store, and MLflow services.

Run inference
-------------

Start from ``examples/inference-vllm.yaml`` and set the image, model identity,
resource requests, and optional accelerator requirement:

.. code-block:: yaml

   spec:
     model: {name: my-model, version: v1, uri: s3://models/my-model}
     container: {image: registry.example/research/my-model:v1, port: 8000}
     accelerator:
       count: 1
       fit:
         modules:
           - {name: model-weights, memoryGiB: 26, distribution: sharded}
           - {name: kv-cache, memoryGiB: 8, distribution: sharded}
           - {name: runtime, memoryGiB: 3, distribution: replicated}
         safetyMarginPercent: 10
     autoscaling: {enabled: true, minReplicas: 0, maxReplicas: 4}

``scripts/deploy_inference_service.py`` builds this custom resource as
structured JSON and prints it for review by default. Add ``--apply`` only after
review. The operator then creates the standard Deployment, Service, KEDA
ScaledObject, and Cilium HTTPRoute; model teams do not maintain those objects
separately.

See :doc:`WORKLOAD_TEMPLATES` for public and private Hugging Face repositories,
model caching, approved downloader init containers, and source-image guidance.

Run training
------------

Start from ``examples/training-pytorch.yaml``. Use a new ``TrainingRun`` name
for every attempt because Kubernetes Job templates are immutable. Set resource
requests realistically so the scheduler and capacity provider select the right
node. Use ``activeDeadlineSeconds`` and ``backoffLimit`` to bound spend.

Register one immutable ``Dataset`` object per version, then reference it with an
exact ``expectedVersion``. See :doc:`DATASETS` and
``examples/training-with-dataset.yaml``. The Job receives resolved metadata in
``MODEL_FLEET_DATASETS_JSON``; a registered PVC is mounted read-only. Registered
status does not verify a remote URI.

Validate before promotion
-------------------------

The reusable examples under ``examples/validation`` test GPU visibility and an
OpenAI-compatible model API. Record model version, image digest, dataset or
prompt-suite version, quality thresholds, throughput, latency, and estimated
cost in MLflow before promotion.

Cloud data access
-----------------

Ask the platform operator for a dedicated Kubernetes ServiceAccount mapped to
AWS IRSA/EKS Pod Identity or GKE Workload Identity. Reference it with
``spec.serviceAccountName`` and set ``automountServiceAccountToken: true`` only
when the process actually calls Kubernetes. Grant object access to the model or
dataset prefix, not an entire account or project.
