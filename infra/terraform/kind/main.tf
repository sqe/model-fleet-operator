locals {
  repository_root = abspath("${path.module}/../../..")
  config_path     = "${path.module}/.generated-${var.cluster_name}.yaml"
  kube_context    = "kind-${var.cluster_name}"
}

resource "local_file" "kind_config" {
  filename = local.config_path
  content = templatefile("${path.module}/kind.yaml.tftpl", {
    cluster_name = var.cluster_name
    node_image   = var.node_image
    worker_count = var.worker_count
    http_port    = var.http_port
    https_port   = var.https_port
  })
}

resource "terraform_data" "cluster" {
  input = {
    cluster_name = var.cluster_name
    config_path  = local_file.kind_config.filename
  }
  triggers_replace = [local_file.kind_config.content_sha256]

  provisioner "local-exec" {
    command = "${path.module}/scripts/create-cluster.sh '${var.cluster_name}' '${local_file.kind_config.filename}'"
  }

  provisioner "local-exec" {
    when       = destroy
    command    = "kind delete cluster --name '${self.input.cluster_name}'"
    on_failure = continue
  }
}

resource "terraform_data" "platform" {
  input = {
    cluster_name = var.cluster_name
  }
  triggers_replace = [
    var.cilium_version,
    var.gateway_api_version,
    var.keda_version,
    var.operator_image,
    var.build_operator_image,
    filesha256("${local.repository_root}/charts/model-fleet-operator/Chart.yaml"),
    filesha256("${local.repository_root}/Dockerfile"),
    filesha256("${local.repository_root}/infra/cilium/hubble-values.yaml"),
  ]

  provisioner "local-exec" {
    command = join(" ", [
      "${path.module}/scripts/bootstrap.sh",
      "'${var.cluster_name}'",
      "'${var.cilium_version}'",
      "'${var.gateway_api_version}'",
      "'${var.keda_version}'",
      "'${var.operator_image}'",
      "'${var.build_operator_image}'",
      "'${local.repository_root}'",
    ])
  }

  depends_on = [terraform_data.cluster]
}
