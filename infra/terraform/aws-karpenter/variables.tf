variable "aws_region" {
  description = "AWS region containing the existing EKS cluster."
  type        = string
}

variable "cluster_name" {
  description = "Name of the existing EKS cluster."
  type        = string
}

variable "discovery_tag" {
  description = "Value of karpenter.sh/discovery on eligible subnets and one node security group. Defaults to cluster_name."
  type        = string
  default     = null
}

variable "node_iam_role_name" {
  description = "Stable IAM role name used by EC2NodeClass. Defaults to <cluster_name>-karpenter."
  type        = string
  default     = null
}

variable "karpenter_chart_version" {
  description = "Pinned Karpenter Helm chart version."
  type        = string
  default     = "1.14.1"
}

variable "controller_node_selector" {
  description = "Labels selecting stable, non-Karpenter nodes for the controller."
  type        = map(string)
  default     = {}
}

variable "cpu_capacity_types" {
  type    = list(string)
  default = ["spot", "on-demand"]
}

variable "gpu_capacity_types" {
  type    = list(string)
  default = ["spot", "on-demand"]
}

variable "gpu_instance_families" {
  type    = list(string)
  default = ["g5", "g6", "g6e", "p4", "p5"]
}

variable "enable_gpu_pool" {
  type    = bool
  default = true
}

variable "tags" {
  type    = map(string)
  default = {}
}
