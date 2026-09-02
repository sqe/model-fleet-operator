#!/usr/bin/env bash
set -euo pipefail
dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
namespace=model-artifacts
case "${1:-}" in
  install)
    command -v kubectl >/dev/null
    command -v openssl >/dev/null
    user="minio-$(openssl rand -hex 6)"
    password=$(openssl rand -base64 30 | tr -d '\n')
    kubectl create namespace "$namespace" --dry-run=client -o yaml | kubectl apply -f -
    kubectl -n "$namespace" create secret generic artifact-credentials \
      --from-literal=minio-user="$user" --from-literal=minio-password="$password" \
      --dry-run=client -o yaml | kubectl apply -f -
    kubectl -n "$namespace" delete job create-artifact-buckets --ignore-not-found
    kubectl apply -f "$dir/stack.yaml"
    kubectl -n "$namespace" rollout restart deployment/minio deployment/mlflow
    kubectl -n "$namespace" rollout status deployment/minio --timeout=5m
    kubectl -n "$namespace" wait --for=condition=complete job/create-artifact-buckets --timeout=5m
    kubectl -n "$namespace" rollout status deployment/registry --timeout=5m
    kubectl -n "$namespace" rollout status deployment/mlflow --timeout=5m
    printf 'MinIO user: %s\nMinIO password: %s\nStore these credentials securely.\n' "$user" "$password"
    ;;
  status) kubectl -n "$namespace" get deployments,services,pvc,job/create-artifact-buckets ;;
  uninstall)
    kubectl -n "$namespace" delete deployment registry minio mlflow --ignore-not-found
    kubectl -n "$namespace" delete service registry minio mlflow --ignore-not-found
    kubectl -n "$namespace" delete job create-artifact-buckets --ignore-not-found
    kubectl -n "$namespace" delete secret artifact-credentials --ignore-not-found
    echo "PVCs and namespace retained. Use the guarded purge command to remove them."
    ;;
  purge)
    echo "Refusing destructive purge without CONFIRM_PURGE=yes" >&2
    [[ ${CONFIRM_PURGE:-} == yes ]] || exit 2
    kubectl delete namespace "$namespace" --ignore-not-found
    ;;
  *) echo "Usage: $0 {install|status|uninstall|purge}" >&2; exit 2 ;;
esac
