# AWS Karpenter capacity

Adds Karpenter 1.14.1, its IAM roles, EKS Pod Identity association,
interruption queue, and Model Fleet CPU/GPU NodePools to an **existing** EKS
cluster. It does not create or alter the EKS control plane, VPC, or CNI.

## Prerequisites

- AWS CLI credentials and cluster-admin access to the existing EKS cluster.
- EKS Pod Identity Agent installed on the cluster.
- At least one stable managed node for the Karpenter controller. Label those
  nodes and set `controller_node_selector`. Karpenter cannot bootstrap itself.
- Eligible private subnets and exactly one node security group tagged
  `karpenter.sh/discovery=<discovery_tag>`. The default value is the cluster
  name.
- NVIDIA device plugin or GPU Operator if the GPU NodePool is enabled.

```sh
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform plan
terraform apply
```

Install the main add-ons module afterward with `profile = "aws"` and
`karpenter_available = true`. Model Fleet GPU examples select
`accelerator: nvidia`, which matches the GPU NodePool. CPU workloads without a
selector use the CPU pool.

Before destroying this root, remove Model Fleet workloads and wait for
Karpenter-owned nodes to terminate. The controller must stay on stable cluster
capacity throughout teardown.
