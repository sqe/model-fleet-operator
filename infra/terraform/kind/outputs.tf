output "cluster_name" {
  value = var.cluster_name
}

output "kube_context" {
  value = local.kube_context
}

output "gateway_http_url" {
  value = "http://localhost:${var.http_port}"
}

output "gateway_https_url" {
  value = "https://localhost:${var.https_port}"
}

output "verify_command" {
  value = "kubectl --context ${local.kube_context} get gateway,httproute -A && kubectl --context ${local.kube_context} -n kube-system get pods,svc -l k8s-app=hubble-relay"
}

output "hubble_ui_command" {
  value = "cilium hubble ui --context ${local.kube_context}"
}
