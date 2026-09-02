output "operator_release" { value = helm_release.model_fleet_operator.name }
output "operator_namespace" { value = helm_release.model_fleet_operator.namespace }
output "keda_release" { value = helm_release.keda.name }
output "gpu_operator_release" {
  value = try(helm_release.nvidia_gpu_operator[0].name, null)
}
output "karpenter_available" {
  description = "Whether workloads are told that externally provisioned Karpenter is available."
  value       = var.karpenter_available
}
