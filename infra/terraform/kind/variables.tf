variable "cluster_name" {
  description = "Name of the local kind cluster."
  type        = string
  default     = "model-fleet"
}

variable "node_image" {
  description = "Pinned kind node image."
  type        = string
  default     = "kindest/node:v1.34.0"
}

variable "worker_count" {
  description = "Number of kind worker containers."
  type        = number
  default     = 1
  validation {
    condition     = var.worker_count >= 1 && var.worker_count <= 8
    error_message = "worker_count must be between 1 and 8."
  }
}

variable "http_port" {
  description = "Host port mapped to the Cilium Gateway HTTP listener."
  type        = number
  default     = 8080
}

variable "https_port" {
  description = "Host port mapped to the Cilium Gateway HTTPS listener."
  type        = number
  default     = 8443
}

variable "cilium_version" {
  type    = string
  default = "1.20.1"
}

variable "gateway_api_version" {
  type    = string
  default = "1.4.1"
}

variable "keda_version" {
  description = "KEDA Helm chart version."
  type        = string
  default     = "2.17.2"
}

variable "operator_image" {
  description = "Local image name loaded into kind."
  type        = string
  default     = "model-fleet-operator:dev"
}

variable "build_operator_image" {
  description = "Build the operator image before loading it into kind."
  type        = bool
  default     = true
}
