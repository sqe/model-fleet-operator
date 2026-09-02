# Cilium Gateway requirements

Model Fleet uses Gateway API rather than Kubernetes Ingress. The cluster must
have the standard Gateway API CRDs and Cilium configured with:

```yaml
kubeProxyReplacement: true
gatewayAPI:
  enabled: true
```

## Hubble observability

`infra/cilium/hubble-values.yaml` enables Hubble Relay, the Hubble UI,
OpenMetrics, and bounded workload/namespace context labels for DNS, drop, TCP,
flow, ICMP, and HTTP telemetry. The Terraform kind environment applies this
file automatically. On an existing cluster, merge it into the values managed by
the Cilium release owner rather than creating a second Helm release:

```bash
helm upgrade cilium cilium/cilium -n kube-system --reuse-values \
  -f infra/cilium/hubble-values.yaml
kubectl -n kube-system rollout status daemonset/cilium
kubectl -n kube-system rollout status deployment/hubble-relay
cilium hubble ui
```

Review metric-label cardinality before using pod or identity labels in a large
cluster. The included defaults use namespace and workload dimensions and expose
exemplars for HTTP traces without including request paths or headers.

The local kind environment additionally uses Cilium Gateway host-network mode.
This avoids requiring a separate LoadBalancer implementation inside Docker. Its
listeners bind to ports 80 and 443 in the kind node, which are mapped to host
ports 8080 and 8443.

For EKS, GKE, and MicroK8s, choose Cilium's LoadBalancer or host-network mode as
appropriate for the cluster. Verify the shared gateway before deploying models:

```bash
kubectl get gatewayclass cilium
kubectl -n model-fleet-system get gateway model-fleet
kubectl -n model-fleet-system describe gateway model-fleet
```

`Accepted=True` and `Programmed=True` are required. Each inference route should
then show `Accepted=True` and `ResolvedRefs=True`:

```bash
kubectl get httproute -A
kubectl describe httproute -n models granite-3
```

Cross-namespace routes are allowed by the chart's shared Gateway. Access to
creating `InferenceService` resources should therefore be treated as permission
to publish a route on that Gateway.
