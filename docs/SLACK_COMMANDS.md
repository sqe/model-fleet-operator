# Slack command reference

The Slack app uses Socket Mode and the `/fleet` command. Read commands may be
available more broadly; write commands require a user ID in
`SLACK_ALLOWED_USER_IDS`. An empty write allowlist is read-only. Namespace and
channel policy still applies.

The app also provides a private App Home with Refresh, All costs, Wake, Auto,
and Sleep buttons. Workload controls cover inference services and managed agents
in the default workload namespace. All costs shows global billing-export data;
Sleep uses Slack's confirmation dialog. Button actions pass through the same
user allowlist, audit annotations, Kafka transport, and Kubernetes intent
patches as slash commands. Channel allowlists apply to conversations; App Home
has no channel and remains private to its user.

The Slack agent and reconciler are separate Deployments with separate service
accounts and RBAC. Install both from the checked-in example after creating the
credential Secret:

```bash
helm upgrade --install model-fleet charts/model-fleet-operator \
  --namespace model-fleet-system --create-namespace \
  --values examples/slack-operator-values.yaml
```

The example defaults to direct Kubernetes intent patches. Enabling Kafka makes
replay-safe commands durable; quota requests always call the provider directly.

```{mermaid}
sequenceDiagram
  actor U as Slack user
  participant B as Slack bot
  participant K as Kubernetes API
  participant P as AWS or GCP API
  U->>B: /fleet command
  B->>B: authorize and validate confirmation
  alt Kubernetes intent
    B->>K: read or patch custom resource
  else quota request
    B->>P: call provider with Slack pod identity
  end
  B-->>U: result or actionable error
```

## Commands

| Command | Effect |
|---|---|
| `status [namespace]` | List fleet workload state. |
| `cost [namespace]` | Report available Prometheus/OpenCost estimates. |
| `snapshot [namespace]` | Upload the configured Grafana dashboard image. |
| `wake <namespace/name>` | Force an inference service active. |
| `wake agent <namespace/name>` | Restore a managed agent's active replicas. |
| `auto <namespace/name>` | Return an inference service to automatic scaling. |
| `sleep <namespace/name> confirm` | Suspend an inference service. |
| `sleep agent <namespace/name> confirm` | Scale a managed agent to zero. |
| `run <skill> <prompt>` | Route an allowlisted user's task through the agent supervisor and Kafka. |
| `pause training <namespace/name>` | Suspend a training run. |
| `resume training <namespace/name>` | Resume a training run. |
| `cancel training <namespace/name> confirm` | Cancel a training run. |
| `help` | Show command help. |

Square brackets mark optional arguments; angle brackets are placeholders. Enter
commands as `/fleet status models`, for example. Confirmation must be the final
literal `confirm` where shown.

Agent commands patch `AgentRegistration.spec.suspend`. The operator scales the
linked Deployment, so the custom resource remains the source of truth. Only a
registration with `spec.runtime.deploymentName` is managed. The Deployment must
carry the metadata annotation
`fleet.sqe.io/agent-registration: <registration-name>`. This explicit link
prevents a registration from scaling an unrelated Deployment. Sleeping an
agent preserves its registration and Service so the control plane can wake it.

`run` does not call a specialist agent directly. When
`slack.agentSupervisor.enabled=true`, the bot submits the exact skill and prompt
to the authenticated supervisor; the supervisor resolves its registered Agent
Card and publishes JSON-RPC to Kafka. This keeps Slack outside agent credentials
and makes task execution durable and observable. Put `control-plane-api-key` in
the Slack Secret and set `slack.agentSupervisor.url` to the supervisor Service.

## Cloud quota requests

```text
quota aws <service-code> <quota-code> <desired-value> [region] confirm
quota gcp <project-number> <service> <quota-id> <desired-value> [key=value ...] confirm
```

Quota submission is disabled unless `slack.quotaRequests.enabled=true` in Helm,
which sets `SLACK_QUOTA_REQUESTS_ENABLED=true` on the Slack pod. It is available
only to write-authorized users and always requires `confirm`. Calls use the
cloud identity of the Slack pod. Grant that identity only the quota read/request
permissions required for approved accounts or projects.

For AWS, region defaults to `AWS_REGION`, then `AWS_DEFAULT_REGION`. The bot
checks the current quota and pending requests before submitting through Service
Quotas. For GCP, dimensions follow the desired value, for example
`region=us-central1 gpu_family=NVIDIA_H100`. `QUOTA_CONTACT_EMAIL` and
`QUOTA_JUSTIFICATION` configure GCP request metadata. The bot updates a
deterministically named, declarative `QuotaPreference`, so retries target the
same preference.

```yaml
slack:
  quotaRequests:
    enabled: true
    contactEmail: platform@example.com
    justification: GPU capacity for approved model workloads
serviceAccount:
  slack:
    annotations:
      # Use the annotation for EKS IRSA/Pod Identity or GKE Workload Identity.
      provider-specific-identity-annotation: provider-service-account
```

Start with the provider rulesets under `config/permissions`. Google Cloud also
requires the Cloud Quotas API to be enabled in each target project.

Provider acceptance means only that a request was submitted or updated. It does
not guarantee approval, GPU stock, zonal availability, or instance capacity.
Track the request in the provider console as described in [Runbooks](RUNBOOKS.md).

Quota operations call external provider APIs directly and bypass Kafka.
Provider quota requests are not equivalent to replay-safe Kubernetes desired
state, so they are intentionally outside the durable command topic.

## Configuration notes

`PROMETHEUS_URL` enables `cost`; `GRAFANA_URL` and a read-only Grafana token
enable `snapshot`. See [Observability](OBSERVABILITY.md). The bot does not store
the rendered image on disk. Configure Socket Mode credentials and permissions
as described in [Security and permissions](security-permissions.rst).

In the Slack app configuration, enable the **Home Tab** under App Home, enable
Interactivity, and subscribe the bot to the `app_home_opened` event. Socket Mode
delivers slash commands, events, and button actions without exposing an inbound
HTTP endpoint. Reinstall the Slack app after changing scopes or subscriptions.
`config/slack-app-manifest.yaml` contains the complete reproducible app
configuration. Create a separate app-level token with `connections:write` for
`SLACK_APP_TOKEN`; that scope does not belong in the bot token section.
