Custom-resource API
===================

API inventory
-------------

.. list-table::
   :header-rows: 1

   * - Surface
     - Contract
     - Owner
   * - Kubernetes API
     - Dataset, InferenceService, TrainingRun, AgentRegistration, AgentTask
     - Model Fleet operator
   * - Generated resources
     - Deployment, Service, Job, ScaledObject, HTTPRoute
     - Model Fleet and Kubernetes controllers
   * - Slack
     - Read, workload-intent, cost, snapshot, and quota commands
     - Slack service
   * - Durable commands
     - Versioned JSON-RPC envelopes on Kafka
     - Command worker
   * - Agent transports
     - Authenticated HTTP JSON-RPC and protobuf/gRPC
     - Agent gateway and services
   * - Metrics
     - Prometheus/OpenMetrics from Model Fleet and integrated exporters
     - Producers listed in :doc:`OBSERVABILITY`
   * - Provider quota APIs
     - AWS Service Quotas and Google Cloud Quotas
     - Provider, called only by the enabled Slack service

InferenceService
----------------

Required fields are ``spec.model.name`` and ``spec.container.image``. The model
section records identity and an optional artifact URI. The container section
accepts command, arguments, environment references, port, and Kubernetes
resources. ``spec.accelerator`` selects a portable GPU memory class. It accepts
either a measured ``minimumMemoryGiB`` or ``fit.modules`` memory budgets.
Sharded module memory is divided by GPU count; replicated memory is added per
device; then ``safetyMarginPercent`` is applied. Optional ``products`` become
required ``nvidia.com/gpu.product`` node affinity. The operator rejects an
explicit class too small for the calculated result.
``spec.autoscaling`` configures KEDA, ``spec.gateway`` creates a Cilium
``HTTPRoute``, and ``suspend``/``forceActive`` expose explicit operational
intent. ``spec.initContainers`` supports approved model or configuration
prefetch steps that share a declared volume with the serving container.

TrainingRun
-----------

``spec.image`` is required. Parallelism, completions, retries, deadlines,
volumes, accelerator selection, suspension, and cancellation map directly to a
Kubernetes Job. ``spec.datasets`` references same-namespace ``Dataset`` objects
with a logical ``name``, ``datasetRef``, exact ``expectedVersion``, and optional
``mountPath``. The operator checks the version and ServiceAccount allowlist,
exports ``MODEL_FLEET_DATASETS_JSON``, and mounts registered PVCs read-only.
Create a new resource for a changed Job template. See :doc:`DATASETS`.

Dataset
-------

``Dataset`` registers one immutable version. Required fields are ``uri``,
``version``, ``format``, ``owner``, and ``classification``. Optional fields are
``description``, ``license``, ``checksum``, ``sizeBytes``, ``schema``, named
``splits``, ``allowedServiceAccounts``, and ``storage.pvc``. Registered status
does not verify remote bytes or cloud access.

AgentRegistration and AgentTask
-------------------------------

``AgentRegistration`` stores versioned agent cards, skills, and Kafka/HTTP/gRPC
transports. Optional ``spec.runtime.deploymentName`` and ``activeReplicas`` link
an explicitly annotated Deployment for start/stop control. ``spec.suspend`` is
the durable replica intent. ``AgentTask`` stores routing and completion metadata only. The
versioned JSON-RPC and protobuf contracts live in ``src/modelfleet/protocol.py``
and ``proto/modelfleet/agent/v1/agent.proto``.

Schema source of truth
----------------------

The complete structural schemas, defaults, enums, and validation constraints
are distributed under ``charts/model-fleet-operator/crds``. Use
``kubectl explain dataset.spec`` or ``kubectl explain trainingrun.spec`` after
installation for the schema installed in a cluster.

Interfaces and generated resources
----------------------------------

``InferenceService`` generates a Deployment, Service, optional KEDA
``ScaledObject``, and optional Cilium ``HTTPRoute``. ``TrainingRun`` generates a
Job. ``Dataset`` is registration metadata and may add a read-only PVC volume to
that Job. Owner references connect generated resources to workload CRs.

Slack provides the bounded operations in :doc:`SLACK_COMMANDS`. Optional
control and agent services use versioned JSON-RPC over authenticated HTTP or
Kafka; agent runtimes also expose HTTP health, Agent Card, and Prometheus
endpoints, with matching protobuf/gRPC contracts. Prometheus metrics are
documented in :doc:`OBSERVABILITY`.

AWS Service Quotas and GCP Cloud Quotas are provider APIs used only for enabled
Slack quota requests. They use the Slack pod identity and do not pass through
Kafka. Capacity provisioning remains provider-specific; this interface list
does not imply that IAM, quota, machine, or networking APIs are portable.
