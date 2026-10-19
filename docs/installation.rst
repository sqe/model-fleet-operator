Installation
============

Supported paths
---------------

.. mermaid::
   :caption: Pick an installation path by where capacity comes from

   flowchart TD
     start{Where will Model Fleet run?}
     start -->|Laptop or CI| kind[kind Terraform root]
     start -->|AWS| eks[Existing EKS cluster]
     start -->|Google Cloud| gke[Existing GKE cluster]
     start -->|Local servers| metal[MicroK8s or Kubernetes]
     kind --> local[Installs Cilium, Hubble, KEDA, and Model Fleet]
     eks --> aws[AWS Karpenter root, then add-ons root]
     gke --> google[GKE node autoscaling, then add-ons root]
     metal --> bare[Fixed nodes or machine provider, then add-ons root]

     classDef complete fill:#eaf7f2,stroke:#17805c,color:#172033
     class local complete

The fastest complete installation is the Terraform-managed kind environment:

.. code-block:: bash

   make bootstrap
   make kind-up
   kubectl --context kind-model-fleet get pods -A
   kubectl --context kind-model-fleet get gateway,httproute -A

It creates one control plane and one worker, Gateway API CRDs, Cilium, Hubble
Relay/UI and metrics, KEDA, and Model Fleet. It is CPU-only by default.

Controller-only OLM installation
---------------------------------

The initial alpha OLM package is under validation; OperatorHub availability
requires a separate upstream review and is not yet confirmed. Once the verified
``v0.1.0`` release is available, its ``install.yaml`` asset installs a project
CatalogSource and Subscription. It is not an OperatorHub listing.

Use Kubernetes 1.34+ with classic OLM already installed in the ``olm`` namespace,
and permission to install cluster-wide CRDs and RBAC:

.. code-block:: bash

   kubectl apply -f https://github.com/sqe/model-fleet-operator/releases/download/v0.1.0/install.yaml
   kubectl -n model-fleet-system wait --for=create csv/model-fleet-operator.v0.1.0 --timeout=5m
   kubectl -n model-fleet-system wait --for=jsonpath='{.status.phase}'=Succeeded \
     csv/model-fleet-operator.v0.1.0 --timeout=5m
   kubectl -n model-fleet-system rollout status deployment/model-fleet-operator

From a matching source checkout, try the CPU-only examples:

.. code-block:: bash

   kubectl create namespace models
   kubectl -n models apply -f config/olm/examples.yaml
   kubectl -n models rollout status deployment/model-echo
   kubectl -n models wait --for=condition=complete job/cpu-smoke-test --timeout=5m
   kubectl -n models get inferenceservices,trainingruns,datasets,agentregistrations

This installs one AllNamespaces controller with four CRDs. The examples create
an echo Deployment/Service and Python Job and register dataset/agent metadata.
They do not fetch data, start an agent, or require a GPU. Replace example URIs
and endpoints before using them for real work.

Use Helm instead for optional Slack/Kafka, the model gateway, registry,
supervisor, Fleet agent, and ``AgentTask`` services. Do not run an OLM controller
alongside a Helm controller in the same cluster. KEDA, Gateway API, storage,
GPU drivers/device plugins, and workload credentials remain separate
prerequisites when their corresponding features are enabled.

Existing MicroK8s, EKS, or GKE cluster
--------------------------------------

Before installation, verify:

* Kubernetes and Helm access with permission to install CRDs and ClusterRoles.
* Gateway API CRDs and a Cilium ``GatewayClass`` named ``cilium``.
* KEDA when ``InferenceService.spec.autoscaling.enabled`` will be used.
* A default StorageClass for examples that claim persistent volumes; referenced
  Dataset PVCs must already exist in the TrainingRun namespace.
* A GPU device plugin and matching node labels for GPU workloads.

Then use the add-ons Terraform root, which installs pinned KEDA and Model Fleet:

.. code-block:: bash

   cd infra/terraform/addons
   cp terraform.tfvars.example terraform.tfvars
   terraform init
   terraform plan
   terraform apply

After choosing the platform, render a workload identity ruleset from the
matching ``config/permissions/*.example.json`` file. See
:ref:`selectable-workload-rulesets` for the review-before-apply flow.

Direct Helm installation is supported when prerequisites already exist:

.. code-block:: bash

   helm upgrade --install model-fleet charts/model-fleet-operator \
     --namespace model-fleet-system --create-namespace \
     --set profile=microk8s --wait

AWS capacity
------------

Run ``infra/terraform/aws-karpenter`` against an existing EKS cluster, then run
the add-ons root with ``profile = "aws"`` and
``karpenter_available = true``. The Karpenter controller must run on stable
non-Karpenter capacity so it can provision and remove workload nodes.

GCP capacity
------------

Use GKE node-pool autoscaling or node auto-provisioning. Apply Model Fleet GPU
class labels to matching GPU node pools. This project uses GKE's supported
capacity controls rather than installing an experimental Karpenter provider.

Bare metal and MicroK8s
-----------------------

Label existing GPUs and optionally enable ``gpu_stack_enabled`` with
``gpu_driver_mode = "operator"`` in the add-ons root on compatible Ubuntu
hosts. Dynamic machine scaling requires a separate machine provider. Without
one, KEDA can scale pods, but unschedulable pods wait for capacity.

Definition of ready
-------------------

An installation is ready when the operator is available, the Cilium Gateway is
``Programmed=True``, KEDA can create HPAs, and GPU nodes advertise
``nvidia.com/gpu`` when required. Prometheus, OpenCost, DCGM exporter, Grafana
rendering, Kafka, Slack, and MLflow are optional integrations and report
``unavailable`` when their data sources are absent.

Confirm the installed CRDs include ``datasets.fleet.sqe.io``. The readiness
check does not test remote dataset URIs or cloud IAM. Use the installation
readiness procedure in :doc:`RUNBOOKS` before accepting workloads.
