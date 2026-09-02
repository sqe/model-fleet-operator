provider "helm" {
  kubernetes {
    config_path    = pathexpand(var.kubeconfig_path)
    config_context = var.kube_context
  }
}

resource "helm_release" "keda" {
  name             = "keda"
  repository       = "https://kedacore.github.io/charts"
  chart            = "keda"
  version          = var.keda_chart_version
  namespace        = var.keda_namespace
  create_namespace = true
  atomic           = true
  wait             = true
}

locals {
  gpu_driver_enabled  = var.gpu_driver_mode == "operator"
  gpu_toolkit_enabled = var.gpu_driver_mode == "operator"
}

resource "helm_release" "nvidia_gpu_operator" {
  count = var.gpu_stack_enabled ? 1 : 0

  name             = "gpu-operator"
  repository       = "https://helm.ngc.nvidia.com/nvidia"
  chart            = "gpu-operator"
  version          = var.gpu_operator_chart_version
  namespace        = var.gpu_operator_namespace
  create_namespace = true
  atomic           = true
  wait             = true
  timeout          = 900

  set {
    name  = "driver.enabled"
    value = tostring(local.gpu_driver_enabled)
  }
  set {
    name  = "toolkit.enabled"
    value = tostring(local.gpu_toolkit_enabled)
  }
  set {
    name  = "nfd.enabled"
    value = tostring(var.gpu_nfd_enabled)
  }
  set {
    name  = "dcgmExporter.enabled"
    value = "true"
  }
}

resource "helm_release" "model_fleet_operator" {
  name             = var.release_name
  chart            = abspath("${path.module}/../../../charts/model-fleet-operator")
  namespace        = var.namespace
  create_namespace = true
  atomic           = true
  wait             = true

  set {
    name  = "profile"
    value = var.profile
  }
  set {
    name  = "image.repository"
    value = var.image_repository
  }
  set {
    name  = "image.tag"
    value = var.image_tag
  }
  set {
    name  = "slack.enabled"
    value = tostring(var.slack_enabled)
  }
  set {
    name  = "slack.existingSecret"
    value = var.slack_existing_secret
  }
  set {
    name  = "slack.tokenKey"
    value = var.slack_token_key
  }
  set {
    name  = "slack.appTokenKey"
    value = var.slack_app_token_key
  }
  set {
    name  = "slack.signingSecretKey"
    value = var.slack_signing_secret_key
  }
  set {
    name  = "karpenterAvailable"
    value = tostring(var.karpenter_available)
  }
  set {
    name  = "gateway.enabled"
    value = tostring(var.gateway_enabled)
  }
  set {
    name  = "kafka.enabled"
    value = tostring(var.kafka_enabled)
  }
  set {
    name  = "kafka.bootstrapServers"
    value = var.kafka_bootstrap_servers
  }

  depends_on = [helm_release.keda, helm_release.nvidia_gpu_operator]
}
