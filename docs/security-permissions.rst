RBAC and cloud permissions
==========================

Kubernetes RBAC controls cluster objects. AWS IAM, Google IAM, or a bare-metal
provider API controls machines and storage. These systems have different trust
boundaries, so the project provides separate permission profiles instead of one
universal cloud policy.

.. _selectable-workload-rulesets:

Selectable workload rulesets
----------------------------

Copy the matching example under ``config/permissions``, replace its resource
names, and render it after choosing the target platform:

.. code-block:: bash

   cp config/permissions/aws.example.json permissions.json
   # Edit role ARN and exact artifact prefixes.
   make permissions-render PERMISSION_CONFIG=permissions.json
   cat .generated-permissions/NEXT_STEPS.txt

The AWS ruleset emits an annotated ServiceAccount and a prefix-scoped S3 policy.
The GCP ruleset emits a Workload Identity ServiceAccount and separate bucket
bindings for the built-in object viewer and object creator roles. The bare-metal
ruleset emits a tokenless ServiceAccount with no API permissions. Mounted
Secrets and PVCs do not require the workload to call the Kubernetes API.
Rendering never applies resources or changes cloud IAM, so the generated
next-step commands remain reviewable before an administrator runs them.

Quota requester rulesets
------------------------

The Slack quota workflow uses a separate cloud identity from model workloads.
Start from ``config/permissions/aws-quota-requester-policy.example.json`` or
``config/permissions/gcp-quota-requester-role.example.yaml``. Restrict AWS
``RequestServiceQuotaIncrease`` to approved quota ARNs. Bind the GCP custom role
only on approved projects. The exact GCP write permissions are
``cloudquotas.quotas.update`` and ``serviceusage.quotas.update``; readback uses
``cloudquotas.quotas.get`` plus the project, service, and Monitoring read
permissions listed in the template. Enable the Cloud Quotas API in each target
project before using the command.

On EKS, annotate the chart's Slack ServiceAccount with its IRSA or Pod Identity
role. On GKE, annotate it with the approved Google service account. Then set
``slack.quotaRequests.enabled=true``. This identity belongs to the Slack service,
not the operator and not a TrainingRun. See :doc:`SLACK_COMMANDS` for command
confirmation and provider behavior.

Kubernetes RBAC installed by the chart
--------------------------------------

**Operator ClusterRole**

* Full lifecycle for Pods, Services, Deployments, Jobs, KEDA ScaledObjects,
  Cilium HTTPRoutes, InferenceServices, and TrainingRuns.
* Read Datasets and AgentRegistrations, then update their status.
* Read Nodes and create or patch Events.
* Update Model Fleet status/finalizers and leader-election Leases.

**Slack ClusterRole, when enabled**

* Read workloads, nodes, Deployments, Jobs, KEDA objects, Gateways, and routes.
* Patch only InferenceService, TrainingRun, and managed AgentRegistration intent.

**Agent control-plane ClusterRole, when enabled**

* Manage AgentRegistration and AgentTask resources and task status.
* Read Nodes and read or patch InferenceService and TrainingRun intent.

The exact executable policy is ``charts/model-fleet-operator/templates/rbac.yaml``.
Installation requires permission to create CRDs, ClusterRoles,
ClusterRoleBindings, Namespaces, ServiceAccounts, Deployments, Services,
ConfigMaps, and Gateways. Restrict who may create Model Fleet custom resources:
that permission is effectively permission to run a container in the namespace.

Workload identities
-------------------

The operator itself needs no cloud identity. Workloads default to no mounted
Kubernetes token. Create a dedicated ServiceAccount per trust boundary when a
model needs object storage, queues, secrets, or cloud APIs.

``Dataset.allowedServiceAccounts`` is a same-namespace admission check during
TrainingRun reconciliation. It does not grant PVC or object-store access.
Kubernetes volume permissions and cloud IAM remain authoritative. A registered
remote Dataset has not been probed by the operator.

AWS EKS
-------

The ``infra/terraform/aws-karpenter`` root uses the official EKS Karpenter
module to create the controller policy, node IAM role, Pod Identity association,
and interruption SQS queue. The controller policy covers the provider actions
needed to discover instance types/images, launch and terminate EC2 capacity,
pass the configured node role, read pricing/SSM data, and consume its queue.
Use the generated policy instead of copying a static action list that can drift
between provider releases. The Terraform caller needs permission to manage
those IAM roles/policies, SQS resources, Pod Identity association, and Helm
resources, plus read the EKS cluster.

Model or training workloads should use IRSA or EKS Pod Identity with only the
service-specific actions they need, for example ``s3:GetObject`` on a model
prefix and ``s3:PutObject`` on a result prefix. Do not attach the Karpenter
controller or node role to workloads.

Google GKE
----------

GKE's managed autoscaler uses Google-managed service agents. Model Fleet does
not need Compute Engine IAM. The installer needs Kubernetes cluster-admin or
equivalent explicit RBAC, along with permission to obtain cluster credentials.
Map workload ServiceAccounts through Workload Identity Federation and grant
narrow roles such as ``roles/storage.objectViewer`` or
``roles/storage.objectCreator`` on specific buckets. Installing or changing
GPU node pools, drivers, Cilium, or node auto-provisioning additionally requires
the corresponding GKE/Compute administration permissions and should remain an
infrastructure pipeline responsibility.

MicroK8s, Proxmox, and bare metal
---------------------------------

Model Fleet needs Kubernetes RBAC only. The NVIDIA GPU Operator requires its
upstream privileged DaemonSet and RBAC permissions to manage host drivers. A
machine autoscaler needs a separate credential for its provider API (for
example, a Proxmox token limited to the target pool, template, datastore, and
network). Model Fleet never consumes that credential directly.

Observability and external services
-----------------------------------

* Slack bot scopes are ``app_mentions:read``, ``chat:write``, ``files:write``,
  ``im:history``, ``im:read``, and ``commands``. The app-level Socket Mode token
  needs ``connections:write``. Enable Interactivity, the Home Tab, and the
  ``app_home_opened`` bot event for interactive controls; that event is a
  subscription, not another OAuth scope.
* Enabled Slack quota requests use the Slack pod's AWS or GCP identity. Keep the
  feature disabled by default and scope that identity to approved quota read and
  request APIs. Write-authorized users still must type ``confirm``.
* Grafana and Prometheus service tokens need read-only dashboard/data access.
* MLflow credentials should permit experiment/run logging only.
* Kafka identities need consume on assigned command/task topics, produce on
  result/event/DLQ topics, and consumer-group access. They do not need cluster
  administration permissions.
* Cilium and GPU Operator use privileged upstream permissions. Install them
  from an infrastructure administration pipeline, not a researcher account.
