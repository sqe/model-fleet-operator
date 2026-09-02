# Troubleshooting

Start with the custom resource, generated child, pod, and recent events. Replace
all `<placeholders>` before running commands.

```bash
kubectl describe <kind> <name> -n <namespace>
kubectl get events -n <namespace> --sort-by=.lastTimestamp
kubectl logs -n model-fleet-system deploy/model-fleet-operator --since=30m
```

## TrainingRun has no Job

Check events for a missing same-namespace Dataset, `expectedVersion` mismatch,
or denied ServiceAccount. Confirm the Dataset is registered and immutable:

```bash
kubectl get trainingrun,dataset -n <namespace> -o wide
kubectl describe dataset <dataset> -n <namespace>
```

## Dataset access fails

`Registered` does not test remote availability. Inspect the Job environment and
pod events, then test with the same image and ServiceAccount. Verify URI,
revision, checksum, cloud IAM, network policy, DNS, and credentials. For PVCs,
verify the claim is Bound, its access mode permits the node attachment, and the
configured `subPath` exists. Dataset PVC mounts are read-only.

For Hugging Face, verify the Secret exists in the same namespace and contains
the `token` key. Do not print the token while testing.

```bash
kubectl get secret huggingface-read-token -n <namespace>
kubectl get pod <pod> -n <namespace> -o jsonpath='{.spec.containers[*].env[*].name}'
```

## GPU pod remains Pending

```bash
kubectl describe pod <pod> -n <namespace>
kubectl get nodes -L model-fleet.sqe.io/gpu-class,nvidia.com/gpu.present
kubectl get events -n <namespace> --field-selector involvedObject.name=<pod>
```

`Insufficient nvidia.com/gpu` with an active capacity controller usually means
node scaling or provider capacity. Label/taint, affinity, PVC-zone, or resource
messages point to scheduling constraints. See [GPU capacity](GPU_CAPACITY.md)
and [Runbooks](RUNBOOKS.md).

## Cloud quota request fails

Confirm `slack.quotaRequests.enabled=true`, the Slack user and channel are
allowlisted, and the command ends in `confirm`. Inspect the Slack deployment,
not the operator deployment:

```bash
kubectl logs -n model-fleet-system deploy/model-fleet-slack --since=30m
kubectl get serviceaccount -n model-fleet-system model-fleet-slack -o yaml
```

AWS requires a region and Service Quotas read/request permissions for the exact
quota. GCP requires a numeric project number, the Cloud Quotas API, workload
identity, and the permissions in the GCP quota requester template. A submitted
request can remain pending, be partially approved, or be denied. A quota
increase does not fix unavailable GPU stock, an unsupported zone, a missing
driver, or Kubernetes scheduling constraints.

## Route or traffic fails

Check Gateway `Programmed`, HTTPRoute `Accepted` and `ResolvedRefs`, endpoints,
then Hubble drops. A healthy route with no endpoints is a workload issue, not a
Gateway issue.

```bash
kubectl get gateway,httproute -A
kubectl get service,endpointslice -n <namespace>
hubble observe --namespace <namespace> --verdict DROPPED --since 10m
```

## Kafka command is delayed or failed

Compare consumer-group lag with worker replicas and topic partitions. Inspect
worker logs and the DLQ. Replay only an authorized, corrected command using its
original workload key. At-least-once delivery can repeat a desired-state patch.

## Managed agent does not start or stop

```bash
kubectl describe agentregistration <name> -n <namespace>
kubectl get deployment <deployment> -n <namespace> -o yaml
```

Verify `spec.runtime.deploymentName`, `spec.runtime.activeReplicas`, and
`spec.suspend`. The Deployment metadata annotation must equal the registration
name. An annotation on the Pod template is not sufficient. A registration
without `runtime` is discoverable but intentionally not scalable.

## Slack App Home is empty or buttons do not work

Enable the Slack Home Tab, Interactivity, and the `app_home_opened` bot event,
then reinstall the app after scope changes. Check Socket Mode connectivity and
Slack pod logs. App Home lists only the configured default namespace and omits
registration-only agents. A user outside `SLACK_ALLOWED_USER_IDS` can inspect
the Home but cannot change workloads.

## Development artifact service is unavailable

```bash
infra/artifacts/artifacts.sh status
kubectl -n model-artifacts get events --sort-by=.lastTimestamp
kubectl -n model-artifacts logs job/create-artifact-buckets
```

Confirm the default StorageClass can bind all three PVCs. A successful MinIO
rollout followed by a failed initialization Job usually indicates credentials,
DNS, or bucket initialization. Plain-HTTP OCI Distribution is for local testing;
cluster nodes require explicit runtime trust or a TLS registry.

## Missing cost, GPU, or network panels

No data is not zero. Confirm Prometheus targets and the required kube-state,
cAdvisor, OpenCost, DCGM, KEDA, Kafka, and Hubble series. See
[Observability](OBSERVABILITY.md) for metric names and limitations.
