# Workload template overlays

Files ending in `.tmpl` contain values that must be replaced before use. Render
them through the team's configuration system, then validate the result with
server-side dry run:

```bash
kubectl apply --dry-run=server -f rendered-workload.yaml
```

`inference-prefetched.yaml.tmpl` demonstrates an approved downloader init
container and shared model volume. `training-from-image.yaml.tmpl` demonstrates
the preferred production training contract with immutable code, explicit model
and dataset revisions, MLflow tracking, durable artifacts, and a bounded run.
