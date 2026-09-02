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
