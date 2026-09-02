# Cluster add-ons

Installs pinned KEDA and the local Model Fleet Operator chart into an **existing**
cluster selected by `kubeconfig_path` and `kube_context`.

```sh
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform plan
terraform apply
```

Profiles are `aws`, `gcp`, `kind`, and `microk8s`. GKE native node autoscaling
and bare-metal capacity are external infrastructure concerns.

Gateway API CRDs and Cilium configured with `gatewayAPI.enabled=true` are
prerequisites. This module installs neither because replacing a cluster CNI is
not a safe add-on operation.

## Slack

Set `slack_enabled = true` and `slack_existing_secret` to a Secret already in
`namespace`. It must contain the keys configured by `slack_token_key` and
`slack_signing_secret_key`. Terraform and chart values never accept literal
tokens.

## Karpenter capability

`karpenter_available` sets the operator's capability marker and is rejected
unless `profile = "aws"`. It does not install Karpenter. Use the adjacent
`aws-karpenter` Terraform root to install the AWS controller, IAM resources,
interruption queue, EC2NodeClass, and CPU/GPU NodePools before enabling it.
Karpenter is not presented as a GCP or bare-metal feature.

## GPU software stack

Set `gpu_stack_enabled = true` to install NVIDIA GPU Operator v26.7.0, Node
Feature Discovery, device plugin, and DCGM exporter. Driver ownership is
explicit because each host must have only one driver manager:

- EKS accelerated/Karpenter AMIs: `gpu_driver_mode = "preinstalled"`.
- GKE nodes using Google's driver installer: `gpu_driver_mode = "preinstalled"`.
  Follow NVIDIA's GKE prerequisites if GPU Operator will own the device plugin.
- Compatible Ubuntu bare metal/MicroK8s: `operator` lets GPU Operator install
  the host driver and toolkit.

The regular kind environment has no GPU passthrough and rejects this option.
Installing the stack does not make an unsupported kernel, OS, or GPU valid.
