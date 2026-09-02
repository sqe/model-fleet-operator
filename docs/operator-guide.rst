Platform operator guide
=======================

Reconciliation
--------------

``InferenceService`` produces a Deployment, Service, optional KEDA
``ScaledObject``, and optional Cilium ``HTTPRoute``. ``TrainingRun`` produces a
Job after resolving its same-namespace ``Dataset`` references. Dataset PVCs are
mounted read-only. Owner references provide garbage collection, while status
and Kubernetes events expose reconciliation failures.

Operational checks
------------------

.. code-block:: bash

   kubectl get isvc,trun,dataset -A
   kubectl get deployment,job,scaledobject,httproute -A
   kubectl logs -n model-fleet-system deploy/model-fleet-operator
   kubectl get events -A --sort-by=.lastTimestamp
   kubectl get nodes -L model-fleet.sqe.io/gpu-class

Scale-to-zero requires an external signal that exists while model pods are
absent, such as Kafka lag, queue depth, or a gateway backlog. A metric emitted
only by the sleeping model cannot wake it.

GPU lifecycle
-------------

Cloud images normally own host drivers. Compatible bare-metal hosts may use the
NVIDIA GPU Operator from ``infra/terraform/addons``. Configure exactly one
driver manager for each node. DCGM exporter supplies utilization, framebuffer
memory, and power metrics.

Cost and network operations
---------------------------

OpenCost rates, model counters, DCGM, and Hubble feed the Grafana dashboard.
Slack operators can run ``/fleet cost [namespace]`` and ``/fleet snapshot
[namespace]``. Cost projections are estimates and exclude any provider charge
not exported to Prometheus.

Use :doc:`RUNBOOKS` for routine procedures, :doc:`TROUBLESHOOTING` for symptom
diagnosis, and :doc:`SLACK_COMMANDS` for the complete command and quota safety
contract.

Upgrades and rollback
---------------------

Render and review Helm output before upgrading. CRDs are installed from the
chart and are not removed automatically on uninstall. Keep workload data in
PVCs or object storage: deleting a ``TrainingRun`` deletes its Job, not an
external artifact store. Back up Kafka and MLflow according to their own
retention policies.
