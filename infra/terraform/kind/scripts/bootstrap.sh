#!/usr/bin/env bash
set -euo pipefail

cluster_name=${1:?cluster name required}
cilium_version=${2:?Cilium version required}
gateway_api_version=${3:?Gateway API version required}
keda_version=${4:?KEDA version required}
operator_image=${5:?operator image required}
build_image=${6:?build image flag required}
repository_root=${7:?repository root required}
context="kind-$cluster_name"

gateway_crds="https://github.com/kubernetes-sigs/gateway-api/releases/download/v${gateway_api_version}/standard-install.yaml"
kubectl --context "$context" apply --server-side -f "$gateway_crds"

helm repo add cilium https://helm.cilium.io --force-update
helm upgrade --install cilium cilium/cilium \
  --kube-context "$context" \
  --namespace kube-system \
  --version "$cilium_version" \
  --values "$repository_root/infra/cilium/hubble-values.yaml" \
  --set ipam.mode=kubernetes \
  --set kubeProxyReplacement=true \
  --set k8sServiceHost="${cluster_name}-control-plane" \
  --set k8sServicePort=6443 \
  --set gatewayAPI.enabled=true \
  --set gatewayAPI.hostNetwork.enabled=true \
  --set envoy.enabled=true \
  --set envoy.securityContext.capabilities.keepCapNetBindService=true \
  --set 'envoy.securityContext.capabilities.envoy[0]=NET_BIND_SERVICE' \
  --wait --timeout 10m

kubectl --context "$context" -n kube-system rollout status daemonset/cilium --timeout=5m
kubectl --context "$context" -n kube-system rollout status deployment/hubble-relay --timeout=5m
kubectl --context "$context" -n kube-system rollout status deployment/hubble-ui --timeout=5m

helm repo add kedacore https://kedacore.github.io/charts --force-update
helm upgrade --install keda kedacore/keda \
  --kube-context "$context" \
  --namespace keda \
  --create-namespace \
  --version "$keda_version" \
  --wait --timeout 10m

if [[ "$build_image" == "true" ]]; then
  docker build -t "$operator_image" "$repository_root"
fi
kind load docker-image "$operator_image" --name "$cluster_name"

image_repository=${operator_image%:*}
image_tag=${operator_image##*:}
helm upgrade --install model-fleet "$repository_root/charts/model-fleet-operator" \
  --kube-context "$context" \
  --namespace model-fleet-system \
  --create-namespace \
  --set profile=kind \
  --set image.repository="$image_repository" \
  --set image.tag="$image_tag" \
  --set image.pullPolicy=IfNotPresent \
  --set gateway.enabled=true \
  --wait --timeout 5m

kubectl --context "$context" wait --for=condition=Programmed \
  gateway/model-fleet -n model-fleet-system --timeout=5m
