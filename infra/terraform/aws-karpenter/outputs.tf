output "controller_release" { value = helm_release.karpenter.name }
output "capacity_release" { value = helm_release.capacity.name }
output "node_iam_role_name" { value = module.karpenter.node_iam_role_name }
output "interruption_queue_name" { value = module.karpenter.queue_name }
output "discovery_tag" { value = local.discovery_tag }
