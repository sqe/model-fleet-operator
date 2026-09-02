# Datasets

`Dataset` registers one immutable dataset version in a namespace. It records
provenance and access intent; it does not copy, validate, or download data.
Create a new resource when the version, URI, metadata, or storage changes.

```{mermaid}
flowchart LR
  D[Dataset version] -->|datasetRef + expectedVersion| T[TrainingRun]
  T --> C{operator checks}
  C -->|version and ServiceAccount allowed| J[Job]
  D -->|PVC configured| P[read-only mount]
  D -->|URI metadata| E[MODEL_FLEET_DATASETS_JSON]
  P --> J
  E --> J
```

## Registration fields

Required fields are `uri`, `version`, `format`, `owner`, and `classification`.
Formats are `parquet`, `json`, `jsonl`, `csv`, `text`, `image`, `audio`,
`webdataset`, or `custom`. Classification is `public`, `internal`,
`confidential`, or `restricted`.

Optional metadata includes `description`, `license`, SHA-256 `checksum`,
`sizeBytes`, an application-defined `schema`, and named `splits`. A split can
have its own URI, checksum, and size. `allowedServiceAccounts` restricts use to
the listed ServiceAccount names. An absent or empty list allows any
ServiceAccount in the namespace. `storage.pvc.claimName` and optional `subPath`
identify data already present on a PVC.

See the complete examples:

* [immutable public Hugging Face registration](../examples/dataset-huggingface-public.yaml)
* [PVC-backed registration](../examples/dataset-pvc.yaml)
* [TrainingRun with an exact version](../examples/training-with-dataset.yaml)

## Use from a TrainingRun

Each `spec.datasets` entry supplies a logical `name`, `datasetRef`, and
`expectedVersion`; `mountPath` is optional. The referenced `Dataset` must be in
the same namespace. Before creating the Job, the operator verifies that the
registered version equals `expectedVersion` and that the TrainingRun's
ServiceAccount is allowed. A mismatch is a reconciliation error, not a fallback
to another version.

The Job receives `MODEL_FLEET_DATASETS_JSON`, a JSON array containing resolved
names, resource names, URIs, versions, formats, checksums, splits, mount paths,
and storage metadata. This variable is reserved and cannot be set in
`TrainingRun.spec.env`. When a referenced Dataset has `storage.pvc`, the
operator mounts that claim read-only at `mountPath`. The application remains
responsible for interpreting remote URI schemes and dataset formats.

## Trust and availability boundaries

`status.phase: Registered` means the Kubernetes registration was accepted. It
does not prove that a remote object exists, that credentials permit access, or
that its bytes match the checksum. Cloud IAM remains authoritative for S3, GCS,
Hugging Face, and other object access. Hugging Face jobs read `HF_TOKEN` from
the Secret documented in [Workload templates](WORKLOAD_TEMPLATES.md). The
ServiceAccount allowlist is an additional operator check, not a replacement for
IAM or PVC permissions.

Dataset URIs are data references. Model Fleet does not clone or execute
arbitrary source repositories. Build code into a reviewed OCI image and use an
approved downloader only when the training application cannot read the URI.

## Inspect

```bash
# Replace <namespace>, <dataset>, and <training-run>.
kubectl get dataset -n <namespace>
kubectl describe dataset <dataset> -n <namespace>
kubectl describe trainingrun <training-run> -n <namespace>
kubectl get events -n <namespace> --sort-by=.lastTimestamp
```
