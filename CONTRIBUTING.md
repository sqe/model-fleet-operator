# Contributing

Open an issue before making an API change. Describe the Kubernetes resources the
change should produce and how it behaves on kind, MicroK8s, EKS, and GKE. Cloud
specific settings belong in infrastructure examples rather than the core API.

## Development

```bash
make bootstrap
make validate
```

Resource construction belongs in `src/modelfleet/resources.py` and must remain
testable without a cluster. Reconciliation belongs in `operator.py`. Slack may
change custom-resource intent, but it must not bypass reconciliation or call a
cloud provider directly.

New fields require all of the following:

1. A structural CRD schema with validation.
2. A resource-builder test.
3. A concise example or documentation update.
4. Backward-compatible behavior, or a clear migration note while the API is
   `v1alpha1`.

Do not commit credentials, kubeconfigs, Terraform state, generated plans, model
weights, or training data.
