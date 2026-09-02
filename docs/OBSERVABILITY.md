# Model Fleet observability

`dashboards/model-fleet.json` is a Grafana dashboard backed by Prometheus. Import
it directly, or let a Grafana sidecar discover it from the Helm release:

```yaml
grafanaDashboard:
  enabled: true
  label: grafana_dashboard
  labelValue: "1"
  key: model-fleet.json
```

The label and value must match the Grafana sidecar's label selector. The key
becomes the dashboard filename. This feature is disabled by default and does not
install Grafana or other monitoring components.

The dashboard is one operational view with five labeled sections:

1. Compute and accelerator utilization
2. Inference, training, scaling, and storage
3. Messaging and model economics
4. Cilium and Hubble network observability
5. Cost and efficiency summary

Hubble is included because it shows whether requests reached a model, failed
during DNS or TCP setup, or were dropped by policy. Its panels stay in a
separate row so network data does not obscure resource and model economics. The
shared namespace and workload filters cover CPU and memory use, utilization
against requests, GPU use, GPU memory and power, PVC capacity, replicas, Jobs,
Kafka lag, tokens, spend, cost per token, and OpenCost rates.

## Prerequisites and no-data behavior

* **Prometheus** must scrape the sources below. Select it with the dashboard's
  `Prometheus` variable.
* **kube-state-metrics** provides namespace and workload discovery, Deployment
  replicas, HPA/KEDA replica state, training Job state, and pod-to-node joins.
  Dependent panels and variables show no data when it is absent.
* **kubelet/cAdvisor metrics** provide CPU, memory, and
  `kubelet_volume_stats_*`. PVC panels require volume statistics from the
  kubelet and CSI driver.
* **NVIDIA GPU Operator or dcgm-exporter** must expose
  `DCGM_FI_DEV_GPU_UTIL`, `DCGM_FI_DEV_FB_USED`, and
  `DCGM_FI_DEV_POWER_USAGE` with `namespace`, `pod`, and `gpu` labels. GPU
  panels show no data for CPU-only workloads or when these metrics are absent.
* **KEDA metrics** provide `keda_scaler_metrics_value`. HPA replica series
  remain available through kube-state-metrics when KEDA metrics are absent.
* **Kafka exporter metrics** must expose `kafka_consumergroup_lag` with
  `consumergroup` and `topic` labels.
* **OpenCost** must expose `node_total_hourly_cost`, `node_gpu_hourly_cost`, and
  `pv_hourly_cost`. kube-state-metrics supplies the pod-to-node and PVC-to-volume
  joins. Missing components remain unavailable instead of being counted as
  zero. Node rates estimate the full cost of nodes hosting selected pods and do
  not provide chargeback-quality pod allocation.
* **Cloud billing exporters** can publish cumulative
  `model_fleet_cloud_cost_usd_total` counters with `provider`, `service`, and
  `category` labels. Export AWS CUR or Cost Explorer results and GCP Cloud
  Billing export results through the platform's approved collector. Billing
  data is account/project scoped, delayed, and appears only in an all-namespace
  report; it is not attributed to a namespace. Publish every billed service and
  use stable categories such as `compute`, `gpu`, `storage`, `network`,
  `database`, and `security`; the reporter preserves other categories too.
* **Bare-metal power telemetry** may expose `node_power_usage_watts{node=...}`.
  When it is unavailable, configure a measured whole-node estimate with
  `BARE_METAL_NODE_POWER_WATTS`. Set `BARE_METAL_ELECTRICITY_USD_PER_KWH` to the
  applicable blended electricity rate. Do not enable the fallback for cloud
  nodes or add electricity again when it is already included in an internal
  OpenCost node rate. For the matching Grafana calculation, expose the rate as
  the constant gauge `model_fleet_electricity_usd_per_kwh`; keep it equal to the
  Slack environment value.
* **Cilium Hubble OpenMetrics** provides HTTP, drop, DNS, and TCP panels. Apply
  `infra/cilium/hubble-values.yaml` through the owner of the Cilium release so
  the required namespace and workload labels are present. Panels show no data
  for traffic that Cilium cannot observe or when Prometheus does not scrape
  `hubble-metrics`.

## Model Fleet application metrics

Inference runtimes and adapters must publish the following counters. Token and
inference-cost panels show no data until these metrics are available.

`model_fleet_tokens_total`
: Counter of processed tokens. Required labels are `namespace`, `workload`,
  `model`, and `direction`. The `direction` value must be `input` or `output`.

`model_fleet_inference_cost_usd_total`
: Counter of estimated inference cost in USD. Required labels are `namespace`,
  `workload`, and `model`.

`workload` must match the Kubernetes resource or pod naming prefix used by the
workload selector. Cost per token is the rate of the USD counter divided by the
rate of the token counter. This is an accounting estimate based on the metric
producer's pricing model, not a cloud invoice.

The workload variable is a regular-expression text box with a default value of
`.*`. It is shared by pod, Deployment, HPA, Job, PVC, and application metric
labels. Enter a common resource-name prefix such as `my-model.*` to focus the
dashboard on one workload.

Hubble HTTP exemplars can link metrics to traces when Prometheus and Grafana are
configured with a compatible tracing data source. Hubble does not capture model
prompt or response bodies in this configuration.

## Slack dashboard snapshots

The Slack bot's `snapshot [namespace]` command calls Grafana's `/render/d/...`
API and uploads the returned PNG to the requesting channel. Grafana must have
its image-renderer plugin or remote rendering service configured. Set
`GRAFANA_URL` and store `GRAFANA_SERVICE_ACCOUNT_TOKEN` under the chart's
`slack.grafanaTokenKey`. The token needs only permission to view the dashboard
and its data sources.

Rendering defaults to the previous six hours at 1800×1000 and filters the
dashboard's namespace variable. Override `GRAFANA_SNAPSHOT_FROM`,
`GRAFANA_SNAPSHOT_WIDTH`, `GRAFANA_SNAPSHOT_HEIGHT`, `GRAFANA_ORG_ID`,
`GRAFANA_DASHBOARD_UID`, or `GRAFANA_DASHBOARD_SLUG` through `slack.extraEnv`.
The bot accepts only PNG responses up to 10 MiB and never persists them locally.

## Slack cost reports

Set `PROMETHEUS_URL` through `slack.extraEnv` to enable `/fleet cost
[namespace]`. If Prometheus requires bearer authentication, place its token in
the Slack Secret under `prometheus-service-account-token` (or change
`slack.prometheusTokenKey`). The report includes itemized AWS/GCP billing-export
costs when supplied, OpenCost compute and storage hourly rates, daily/monthly
projections, the GPU cost component, bare-metal power and electricity, previous-
24h model spend and input/output token volume, effective cost per million
tokens, GPU utilization, and aggregate GPU framebuffer usage.

Cloud billing and OpenCost are separate views of overlapping infrastructure and
are never added into one total. The report marks absent billing-export,
OpenCost, power, DCGM, or application metrics as unavailable. OpenCost
projections use current rates and are not invoices. Billing exports can lag and
may still omit taxes, support plans, unsettled usage, or provider-specific
adjustments.

Example bare-metal configuration:

```yaml
slack:
  extraEnv:
    - {name: PROMETHEUS_URL, value: "http://prometheus.monitoring.svc:9090"}
    - {name: BARE_METAL_NODE_POWER_WATTS, value: "650"}
    - {name: BARE_METAL_ELECTRICITY_USD_PER_KWH, value: "0.14"}
```

Use a measured average wattage where possible. The fallback attributes the full
estimated draw of every node hosting a selected workload; it is not pod-level
metering and does not include cooling or facility power unless the configured
number does.

Dataset `Registered` status is not an availability probe. Diagnose dataset
reads from TrainingRun events, pod logs, PVC state, and provider telemetry. The
dashboard also cannot distinguish a provider quota rejection from GPU stock or
zonal constraints without capacity-controller/provider events. See
[Troubleshooting](TROUBLESHOOTING.md).
