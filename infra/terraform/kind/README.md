# Local kind environment

This environment creates a disposable Kubernetes cluster, replaces the default
CNI and kube-proxy with Cilium, enables Gateway API host-network listeners, and
installs Hubble Relay and UI, OpenMetrics, KEDA, and Model Fleet. It follows the
Terraform-driven local workflow in `sqe/robotics-k8s-infra` while keeping the
cluster small enough for operator development.

```bash
terraform init
terraform apply
kubectl --context kind-model-fleet get pods -A
```

The Cilium Gateway binds inside the control-plane container. Kind maps its HTTP
and HTTPS listeners to `127.0.0.1:8080` and `127.0.0.1:8443`. A route with the
hostname `model.localhost` can be tested with:

```bash
curl -H 'Host: model.localhost' http://127.0.0.1:8080/v1/models
```

Inspect end-to-end flows with the Cilium CLI:

```bash
cilium hubble ui --context kind-model-fleet
hubble observe --follow --namespace models
```

Prometheus-compatible Cilium, operator, DNS, drop, TCP, flow, ICMP, and HTTP
metrics are enabled. Hubble's metrics Service carries scrape annotations.

This cluster has no GPU by default. CPU images work unchanged. GPU scheduling
requires a kind GPU configuration or a MicroK8s, EKS, or GKE cluster with GPU
nodes.

Destroy it with `terraform destroy`. This deletes the entire local cluster.
