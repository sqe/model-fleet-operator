# Operator runbooks

Commands use `<namespace>`, `<name>`, `<pod>`, and similar placeholders. Keep
provider-specific account, project, region, zone, and quota values in the
platform team's approved configuration.

```{mermaid}
flowchart TD
  CR[Inspect custom resource] --> Child[Inspect Deployment or Job]
  Child --> Pod[Describe pending or failed pod]
  Pod --> S{Scheduling constraint?}
  S -->|pod resources, labels, PVC| Fix[Fix workload intent]
  S -->|no matching node| N[Inspect capacity controller]
  N --> Q{Quota or provider capacity?}
  Q -->|quota| Request[Submit and track quota request]
  Q -->|stock, zone, instance| Options[Change zone/type or wait]
```

## Installation readiness

```bash
kubectl -n model-fleet-system rollout status deploy/model-fleet-operator
kubectl get crd datasets.fleet.sqe.io trainingruns.fleet.sqe.io inferenceservices.fleet.sqe.io
kubectl get gatewayclass cilium
kubectl -n model-fleet-system get gateway model-fleet
kubectl get pods -A
```

Require `Programmed=True` on the Gateway. If GPU workloads are planned, verify
nodes expose `nvidia.com/gpu` and the expected Model Fleet class. Validate KEDA
only when autoscaling is enabled. Optional integrations may report unavailable.

## Deploy inference

Review image, model revision, resources, route host, and scaling trigger before:

```bash
kubectl apply -f examples/inference-vllm.yaml
kubectl get isvc,deploy,service,scaledobject,httproute -n models
kubectl describe inferenceservice <name> -n <namespace>
```

Use a trigger that exists at zero replicas. Test through the Gateway with the
configured Host header and your normal authentication.

## Submit training with a dataset

Review and apply the immutable registration before the run:

```bash
kubectl apply -f examples/dataset-huggingface-public.yaml
kubectl apply -f examples/training-with-dataset.yaml
kubectl get dataset,trainingrun,job,pod -n models
```

Use a new Dataset object for every version and a new TrainingRun name for every
Job attempt. Verify `expectedVersion`, ServiceAccount allowlist, and cloud IAM.

## Pending GPU pod and scaling owner

```bash
kubectl describe pod <pod> -n <namespace>
kubectl get pod <pod> -n <namespace> -o wide
kubectl get nodes -L model-fleet.sqe.io/gpu-class
kubectl get hpa,scaledobject -n <namespace>
```

KEDA/HPA controls pod count. Karpenter, GKE autoscaling, or an external machine
provider controls node count. If the HPA created a pod and it is Pending, pod
scaling worked. Continue with scheduler events and capacity-controller logs.

## Distinguish GPU quota from capacity constraints

Read the provider controller event or log first. A quota limit names a quota or
limit and current usage. Stock or capacity errors name an unavailable zone,
instance type, accelerator, reservation, or offering. Kubernetes constraints
include unmatched labels/taints, affinity, maximum resources, and PVC topology.
Do not request quota for a stock, zone, instance, driver, or scheduling error.

## Submit and track quota manually

Preferred production flow is the provider console with the team's approval
record. In AWS, open **Service Quotas**, select the service, region, and quota,
request the approved value, then track **Quota request history**. Check pending
requests before creating another. In Google Cloud, open **IAM & Admin > Quotas &
System Limits**, filter by service, quota and dimensions, submit the adjustment,
then track its request status and the resulting QuotaPreference where used.

Authorized Slack users may use the exact commands in
[Slack command reference](SLACK_COMMANDS.md). Submission does not reserve GPU
capacity or guarantee approval.

## Scale to zero and restore

```text
/fleet sleep <namespace/name> confirm
/fleet wake <namespace/name>
/fleet auto <namespace/name>
```

Record prior intent. `sleep` suspends, `wake` forces active, and `auto` restores
automatic scaling. Confirm that pods reach the expected count and that the
external wake metric remains available at zero.

Managed agents use an explicit target:

```text
/fleet sleep agent <namespace/name> confirm
/fleet wake agent <namespace/name>
```

Confirm that the registration has `spec.runtime`, then inspect its linked
Deployment and status. Keep the registry and Slack service active so sleeping
agents remain discoverable and can be woken.

## Cost spike

Run `/fleet cost <namespace>`, inspect current replicas and nodes, then compare
OpenCost, DCGM utilization, Jobs, retry counts, and recent changes. Pause or
suspend only with the workload owner's approval. Estimates can omit egress,
discounts, reservations, support, and taxes.

Run `/fleet cost` without a namespace for itemized account/project billing
exported from AWS, GCP, or another provider. Namespaced reports intentionally
exclude those global billing counters. Compare, but do not add, cloud billed
usage and OpenCost because both can represent the same compute and storage. For
bare metal, verify the wattage and electricity rate against facility telemetry
before using the estimate for decisions.

## Failed dataset access

Follow [Troubleshooting](TROUBLESHOOTING.md). A successful
registration is not a remote read check. Validate with the workload identity,
not an operator or administrator credential.

## Cilium route and Hubble

```bash
kubectl describe gateway model-fleet -n model-fleet-system
kubectl describe httproute <name> -n <namespace>
kubectl get endpointslice -n <namespace>
hubble observe --namespace <namespace> --since 10m
```

Separate route acceptance, backend readiness, DNS, and policy drops. Follow the
Cilium release owner's process before changing shared values.

## Kafka lag and DLQ

Check `kafka_consumergroup_lag`, worker availability, partition count, and
consumer logs. Review authorization and schema errors before replay. Publish a
corrected DLQ command with the original key, then verify an event and committed
offset. Quota provider calls do not use Kafka.

## MLflow

Verify the tracking URL from the training pod, MLflow health, artifact-store
access, and experiment permissions. Do not treat a successful Job as proof that
metrics or artifacts were retained. Restore the tracking database and artifact
store as one documented recovery set.

## Full shutdown and data retention

First suspend inference and training intake, checkpoint active runs, and record
desired state. Back up CRs, MLflow database/artifacts, required PVCs, Kafka
topics/offsets, and external dataset/model locations according to policy. Then
scale optional workers and the operator down through the owning Helm or
infrastructure workflow. Uninstalling the chart does not delete CRDs; deleting a
TrainingRun deletes its owned Job but not external object storage.

## Recovery expectations

Restore infrastructure and durable stores before controllers. Start Kubernetes,
Cilium/Gateway, storage, identity, capacity controllers, KEDA, then Model Fleet
and optional Kafka/MLflow/Slack services. Reconciliation recreates missing child
resources from retained CRs. It cannot recreate remote datasets, model objects,
PVC contents, Kafka history, MLflow data, cloud capacity, or credentials. Test
these dependencies explicitly before resuming automatic scaling.
