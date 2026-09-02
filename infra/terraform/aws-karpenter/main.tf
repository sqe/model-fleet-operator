provider "aws" {
  region = var.aws_region
}

data "aws_eks_cluster" "this" {
  name = var.cluster_name
}

data "aws_ecrpublic_authorization_token" "karpenter" {
  region = "us-east-1"
}

locals {
  discovery_tag      = coalesce(var.discovery_tag, var.cluster_name)
  node_iam_role_name = coalesce(var.node_iam_role_name, "${var.cluster_name}-karpenter")
}

provider "helm" {
  kubernetes = {
    host                   = data.aws_eks_cluster.this.endpoint
    cluster_ca_certificate = base64decode(data.aws_eks_cluster.this.certificate_authority[0].data)
    exec = {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args        = ["eks", "get-token", "--region", var.aws_region, "--cluster-name", var.cluster_name]
    }
  }
}

module "karpenter" {
  source  = "terraform-aws-modules/eks/aws//modules/karpenter"
  version = "21.25.0"

  cluster_name                    = var.cluster_name
  node_iam_role_use_name_prefix   = false
  node_iam_role_name              = local.node_iam_role_name
  create_pod_identity_association = true

  tags = var.tags
}

resource "helm_release" "karpenter" {
  name                = "karpenter"
  namespace           = "kube-system"
  repository          = "oci://public.ecr.aws/karpenter"
  repository_username = data.aws_ecrpublic_authorization_token.karpenter.user_name
  repository_password = data.aws_ecrpublic_authorization_token.karpenter.password
  chart               = "karpenter"
  version             = var.karpenter_chart_version
  atomic              = true
  wait                = true

  values = [yamlencode({
    nodeSelector = var.controller_node_selector
    settings = {
      clusterName       = var.cluster_name
      clusterEndpoint   = data.aws_eks_cluster.this.endpoint
      interruptionQueue = module.karpenter.queue_name
    }
  })]

  depends_on = [module.karpenter]
}

resource "helm_release" "capacity" {
  name      = "model-fleet-capacity"
  namespace = "kube-system"
  chart     = abspath("${path.module}/../../../charts/aws-karpenter-capacity")
  atomic    = true
  wait      = true

  values = [yamlencode({
    clusterName  = var.cluster_name
    discoveryTag = local.discovery_tag
    nodeRoleName = module.karpenter.node_iam_role_name
    cpu = {
      capacityTypes = var.cpu_capacity_types
    }
    gpu = {
      enabled       = var.enable_gpu_pool
      capacityTypes = var.gpu_capacity_types
      families      = var.gpu_instance_families
    }
  })]

  depends_on = [helm_release.karpenter]
}
