variable "kubeconfig_path" {
  description = "Path to the kubeconfig for the existing cluster."
  type        = string
  default     = "~/.kube/config"
}

variable "kube_context" {
  description = "Kubeconfig context to use."
  type        = string
}

variable "profile" {
  description = "Cluster profile: aws, gcp, kind, or microk8s."
  type        = string
  validation {
    condition     = contains(["aws", "gcp", "kind", "microk8s"], var.profile)
    error_message = "profile must be aws, gcp, kind, or microk8s."
  }
}

variable "namespace" {
  type    = string
  default = "model-fleet-system"
}
variable "release_name" {
  type    = string
  default = "model-fleet"
}
variable "image_repository" {
  type    = string
  default = "ghcr.io/sqe/model-fleet-operator"
}
variable "image_tag" {
  type    = string
  default = "0.1.0"
}
variable "slack_enabled" {
  type    = bool
  default = false
}
variable "slack_existing_secret" {
  description = "Name of an existing Secret containing Slack credentials."
  type        = string
  default     = ""
  validation {
    condition     = !var.slack_enabled || length(var.slack_existing_secret) > 0
    error_message = "slack_existing_secret is required when slack_enabled is true."
  }
}
variable "slack_token_key" {
  type    = string
  default = "slack-bot-token"
}
variable "slack_app_token_key" {
  type    = string
  default = "slack-app-token"
}
variable "slack_signing_secret_key" {
  type    = string
  default = "slack-signing-secret"
}

variable "keda_namespace" {
  type    = string
  default = "keda"
}
variable "keda_chart_version" {
  description = "Pinned KEDA Helm chart version."
  type        = string
  default     = "2.17.2"
}

variable "gpu_stack_enabled" {
  description = "Install NVIDIA GPU Operator components, including DCGM exporter and device discovery."
  type        = bool
  default     = false
  validation {
    condition     = !var.gpu_stack_enabled || var.profile != "kind"
    error_message = "The standard kind profile has no GPU passthrough; gpu_stack_enabled is unsupported."
  }
}

variable "gpu_driver_mode" {
  description = "operator installs host drivers; preinstalled uses cloud/local drivers already on each node."
  type        = string
  default     = "preinstalled"
  validation {
    condition     = contains(["operator", "preinstalled"], var.gpu_driver_mode)
    error_message = "gpu_driver_mode must be operator or preinstalled."
  }
}

variable "gpu_operator_chart_version" {
  type    = string
  default = "v26.7.0"
}

variable "gpu_operator_namespace" {
  type    = string
  default = "gpu-operator"
}

variable "gpu_nfd_enabled" {
  description = "Install Node Feature Discovery; disable when the cluster already runs NFD."
  type        = bool
  default     = true
}

variable "karpenter_available" {
  description = "Whether a separately provisioned Karpenter installation is available."
  type        = bool
  default     = false
  validation {
    condition     = !var.karpenter_available || var.profile == "aws"
    error_message = "karpenter_available may only be enabled with the aws profile."
  }
}

variable "gateway_enabled" {
  description = "Create the shared Gateway using the Cilium GatewayClass."
  type        = bool
  default     = true
}

variable "kafka_enabled" {
  description = "Route control commands through Kafka and run the KEDA-scaled command worker."
  type        = bool
  default     = false
}

variable "kafka_bootstrap_servers" {
  description = "Kafka bootstrap servers reachable by KEDA, Slack, and the command worker."
  type        = string
  default     = ""
  validation {
    condition     = !var.kafka_enabled || length(var.kafka_bootstrap_servers) > 0
    error_message = "kafka_bootstrap_servers is required when kafka_enabled is true."
  }
}
