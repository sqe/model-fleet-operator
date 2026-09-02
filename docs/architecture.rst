Architecture
============

The operator converts declarative APIs into standard Kubernetes resources. It
does not proxy model traffic or create cloud machines directly.

This boundary is what makes the system scalable. Each inference service becomes
an independently scalable Deployment and Service; each training run becomes a
bounded Job. Kubernetes schedules them, KEDA changes pod counts, and the
platform capacity provider changes node counts. Model Fleet replaces repeated
model-specific orchestration without replacing proven inference and training
runtimes.

.. mermaid::
   :caption: Model Fleet's control and data planes

   flowchart LR
     user[Researcher or service] -->|applies| api[Workload and Dataset APIs]
     slack[Slack operator] --> control[Control service]
     control -->|patch intent| api
     api --> operator[Model Fleet operator]
     operator --> deploy[Deployments and Jobs]
     operator --> keda[KEDA ScaledObjects]
     operator --> route[Cilium HTTPRoutes]
     keda -->|replica target| deploy
     gateway[Cilium Gateway] -->|model traffic| deploy
     route -. configures .-> gateway
     deploy -->|pending pod| capacity[Capacity provider]
     capacity --> nodes[CPU or GPU nodes]
     metrics[Prometheus, OpenCost, DCGM, Hubble] --> grafana[Grafana]
     grafana --> slack
     dataset[Dataset registration] -->|version, URI, optional PVC| operator
     agent[Managed AgentRegistration] -->|replica intent| operator

     classDef control fill:#eaf2fb,stroke:#2367a9,color:#172033
     classDef runtime fill:#eaf7f2,stroke:#17805c,color:#172033
     class api,operator,control,keda,route control
     class deploy,gateway,capacity,nodes runtime

The solid lines above are runtime or control interactions. Managed agent
registrations can scale an explicitly linked Deployment to zero or restore its
configured replica count. The dotted line is
declarative configuration: an ``HTTPRoute`` does not carry traffic itself.

Deployed components
-------------------

The Helm chart can deploy four independent processes. Enable only the paths the
cluster uses.

.. list-table::
   :header-rows: 1

   * - Component
     - Source of truth
     - Responsibility
   * - Operator
     - Workload, dataset, and agent custom resources
     - Reconcile Deployments, Jobs, Services, KEDA scalers, HTTPRoutes, and
       managed-agent replica intent.
   * - Slack operations agent
     - Slack commands and Kubernetes custom resources
     - Authenticate allowlisted users, report state and cost, and patch workload
       intent through Socket Mode. It never serves model traffic.
   * - Kafka command worker
     - Durable command topic
     - Apply replay-safe commands and commit only after publishing a result.
   * - Agent control plane
     - ``AgentRegistration``, ``AgentTask``, and Kafka task payloads
     - Register skills, route tasks through an allowlisted LLM gateway, and run
       the built-in Fleet Operations agent.

Slack and operator example
--------------------------

The Slack process does not scale Deployments directly. For ``/fleet sleep
models/granite confirm`` it checks channel and user allowlists, adds audit
annotations, and patches ``InferenceService.spec.suspend=true``. The operator
observes that desired state, sets the Deployment to zero, and removes the KEDA
``ScaledObject``. ``/fleet wake`` clears suspension and pins at least one
replica; ``/fleet auto`` returns replica ownership to KEDA.

.. mermaid::
   :caption: Slack control remains declarative

   sequenceDiagram
     actor User as Allowlisted Slack user
     participant Slack as Slack operations agent
     participant API as Kubernetes API
     participant Operator as Model Fleet operator
     participant KEDA
     User->>Slack: /fleet sleep models/granite confirm
     Slack->>Slack: validate target, authorization, confirmation
     Slack->>API: patch InferenceService intent and audit annotations
     API-->>Slack: accepted
     API-->>Operator: watch event
     Operator->>API: Deployment replicas=0; remove ScaledObject
     Slack-->>User: accepted; operator is reconciling

Install both components with the reproducible values example:

.. code-block:: bash

   kubectl -n model-fleet-system create secret generic model-fleet-slack \
     --from-literal=slack-bot-token=xoxb-... \
     --from-literal=slack-app-token=xapp-... \
     --from-literal=slack-signing-secret=...
   helm upgrade --install model-fleet charts/model-fleet-operator \
     --namespace model-fleet-system --create-namespace \
     --values examples/slack-operator-values.yaml

GPU fit example
---------------

An accelerator fit plan describes memory-consuming modules rather than naming
a cloud instance. The operator divides sharded memory by the requested device
count, adds replicated per-device memory, applies the safety margin, and maps
the result to the smallest portable GPU class. Optional product affinity can
further constrain scheduling. Kubernetes creates a Pending pod if no node fits;
the configured capacity provider, not Model Fleet, decides whether a matching
machine can exist.

For example, 70 GiB of weights plus 20 GiB of KV cache sharded over two GPUs,
4 GiB of replicated runtime memory, and a 10% margin requires 54 GiB per GPU.
The resulting pod requests two GPUs and selects ``gpu-80gb``. See
:doc:`GPU_CAPACITY` and ``examples/inference-multi-gpu-fit.yaml``.

Ownership boundaries
--------------------

KEDA owns pod replica decisions. A provider capacity controller owns machine
creation and consolidation. Cilium owns networking, Gateway API, and Hubble.
The NVIDIA GPU Operator or cloud image owns drivers and the device plugin.
OpenCost estimates infrastructure rates, while model runtimes emit token and
model-price counters. Model Fleet only joins those signals and reports them.

.. mermaid::
   :caption: One owner for each scaling layer

   flowchart TB
     signal[Queue, traffic, or Kafka lag] --> keda[KEDA]
     keda -->|sets desired replicas| hpa[HorizontalPodAutoscaler]
     hpa --> pods[Model pods]
     pods -->|pending when capacity is short| scheduler[Kubernetes scheduler]
     scheduler --> machine{Machine capacity}
     machine -->|EKS| karpenter[AWS Karpenter provider]
     machine -->|GKE| gke[GKE autoscaler]
     machine -->|MicroK8s| fixed[Fixed nodes or external provider]
     karpenter --> ready[Ready CPU or GPU node]
     gke --> ready
     fixed --> ready

     classDef decision fill:#fff4dc,stroke:#a96f12,color:#172033
     class machine decision

Portability
-----------

The workload API is portable, but infrastructure APIs are platform-specific.
AWS Karpenter uses ``EC2NodeClass`` and IAM. GKE uses managed node-pool
autoscaling and Google IAM. MicroK8s and other bare-metal clusters use fixed
nodes unless a provider such as Cluster API or a Proxmox integration can create
machines. Karpenter core requires a machine provider and cannot power on
physical hardware by itself.

Durability and security
-----------------------

Kafka delivery is at-least-once. Consumers publish results before committing
offsets, and handlers must be replay-safe. Agent task payloads stay in Kafka.
Kubernetes stores routing and completion metadata only. Workload service-account
tokens default off, and all public control-plane routes default off.

Dataset registration is Kubernetes intent, but remote bytes remain in the
source system. The operator checks exact versions and local ServiceAccount
allowlists without asserting remote existence. Cloud IAM controls remote
access. Slack quota commands call provider APIs directly because external quota
requests are not replay-safe Kubernetes intent and must not be Kafka-replayed.

.. mermaid::
   :caption: Durable command and agent-task delivery

   sequenceDiagram
     actor Person as Slack user or agent client
     participant API as Control service
     participant Kafka
     participant Worker as Replay-safe consumer
     participant K8s as Kubernetes API
     Person->>API: Authenticated command or task
     API->>Kafka: Publish envelope with idempotency key
     Kafka-->>API: Acknowledge durable write
     Worker->>Kafka: Poll uncommitted message
     Worker->>K8s: Patch intent or reconcile task
     Worker->>Kafka: Publish result or event
     Worker->>Kafka: Commit offset
     API-->>Person: Status, cost report, or result
