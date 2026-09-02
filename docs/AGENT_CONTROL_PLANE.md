# Agent control plane

Model Fleet includes optional registry, supervisor, and LLM gateway services.
The registry persists Agent Cards as `AgentRegistration` resources rather than
keeping them in process memory. The supervisor stores routing state in
`AgentTask`. It uses an exact skill ID when one is provided. Otherwise, it asks
the allowlisted OpenAI-compatible gateway to select from registered skills and
publishes a JSON-RPC task to Kafka. Consumers commit offsets after publishing a
result. Task status contains routing and completion metadata only, while Kafka
holds prompts and result bodies outside Kubernetes etcd.

Python agents use `modelfleet.agent_runtime`. Go agents use `sdk/go/agent`.
Both expose `/health`, `/.well-known/agent.json`, and `/metrics`, and commit a
Kafka task only after its result is durably published. MLflow tracing is enabled
by setting `MLFLOW_TRACKING_URI`. Prompts and result bodies are not logged by
default.

The chart also runs a built-in Fleet Operations agent with status, inference
control, and training control skills. Destructive actions require
`confirmed=true`. Agents can use Kafka or authenticated HTTP JSON-RPC at
`/v1/tasks:execute`. `proto/modelfleet/agent/v1/agent.proto` defines matching
unary and streaming gRPC contracts, with Buf generation configured for Python
and Go. The supervisor remains the preferred policy and audit boundary for
agent-to-agent calls.

The built-in agent operates existing `InferenceService` and `TrainingRun`
resources. It does not create a workload from an arbitrary repository URL.
Workload creation remains a reviewed Kubernetes or GitOps change using the
templates in `examples`. This prevents a chat command from turning unreviewed
remote code into a running container. Hugging Face model artifacts can still be
downloaded by the selected runtime or by an approved init container, as
described in [Workload templates and model sources](WORKLOAD_TEMPLATES.md).

## Managed agent runtime

An `AgentRegistration` can optionally link to a same-namespace Deployment:

```yaml
spec:
  runtime:
    deploymentName: research-agent
    activeReplicas: 1
  suspend: false
```

The Deployment opts in with the metadata annotation
`fleet.sqe.io/agent-registration: <registration-name>`. The operator rejects a
mismatched link rather than allowing a registration to scale an arbitrary
Deployment. Slack and the built-in operations agent can set `suspend` to scale
the runtime to zero and clear it to restore `activeReplicas`. Registry metadata
and the Service remain available while the runtime is stopped.

The chart configures this link for its built-in `model-fleet-operations` agent.
Externally deployed agents must add the annotation and runtime reference in
their own manifests.

Enable the services only after Kafka topics and an LLM route exist:

```yaml
kafka:
  enabled: true
  bootstrapServers: kafka.kafka.svc:9092
controlPlane:
  enabled: true
  existingSecret: model-fleet-control
  mlflowTrackingUri: http://mlflow.mlflow.svc:5000
  supervisorModel: router
  routesJson: >-
    {"router":{"base_url":"http://router.models.svc:8000","upstream_model":"router",
    "namespace":"models","workload":"router"}}
```

`model-fleet-control` contains `control-plane-api-key`. Put upstream provider
keys in Secret-backed `controlPlane.extraEnv`. A route names the environment
variable with `api_key_env` and never contains the key itself. Public Cilium
routes are disabled by default. If enabled, keep the bearer key configured and
apply normal network policy and rate limits.

`supervisorRoute`, `gatewayRoute`, and `fleetAgentRoute` are optional external
ingress settings. Each creates a Cilium `HTTPRoute` on the chart's shared
Gateway. They are disabled by default. This project does not install a legacy
Ingress controller alongside Cilium Gateway API.
