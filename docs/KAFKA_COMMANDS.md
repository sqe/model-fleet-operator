# Durable control commands with Kafka

Kafka is optional. When enabled, Slack writes a versioned JSON-RPC command to
`model-fleet.commands.v1` instead of patching a custom resource directly. KEDA
starts the command worker in response to consumer lag. The worker applies the
Kubernetes intent, writes the command ID to the resource annotations, publishes
an `applied` event, and then commits the Kafka offset.

Supported targets are `InferenceService`, `TrainingRun`, and managed
`AgentRegistration` resources. Agent `wake` and confirmed `sleep` operations
use the same replay-safe desired-state patch as inference controls.

Slack AWS and GCP quota requests do not use this path. External provider API
requests have approval workflows and side effects that are not equivalent to
replay-safe Kubernetes intent. They are authorized, confirmed, and submitted
directly as documented in [Slack commands](SLACK_COMMANDS.md).

The design uses idempotent producers, manual consumer commits, replay-safe
effects, and a dead-letter queue.
It provides **at-least-once delivery**, not an exactly-once distributed
transaction. A crash after the Kubernetes patch can replay the command, but all
supported actions set desired state and are safe to repeat.

## Topics

Create these with replication factor 3 in production (1 is acceptable only for
a disposable local broker):

```sh
kafka-topics.sh --bootstrap-server "$BROKERS" --create \
  --topic model-fleet.commands.v1 --partitions 6 --replication-factor 3
kafka-topics.sh --bootstrap-server "$BROKERS" --create \
  --topic model-fleet.events.v1 --partitions 6 --replication-factor 3
kafka-topics.sh --bootstrap-server "$BROKERS" --create \
  --topic model-fleet.commands.dlq.v1 --partitions 3 --replication-factor 3
```

Commands are keyed by `kind/namespace/name`, preserving order for one workload.
Keep `kafka.maxReplicas` no higher than the command topic's partition count.

## Helm configuration

```yaml
kafka:
  enabled: true
  bootstrapServers: kafka.kafka.svc.cluster.local:9092
  maxReplicas: 2
slack:
  enabled: true
  existingSecret: model-fleet-slack
```

For authenticated Kafka, put client variables such as `KAFKA_SASL_USERNAME`
and `KAFKA_SASL_PASSWORD` in `kafka.extraEnv` using Secret references. Set
`kafka.triggerAuthentication` to an existing KEDA `TriggerAuthentication` and
add provider-specific scaler fields under `kafka.scalerMetadata`. No password
belongs in Helm values.

The Kafka cluster remains external. Use MSK or an operated Kafka installation on
EKS. Use a managed service or Kubernetes operator on GKE. A single broker is
sufficient for MicroK8s and kind testing, but it does not provide durable
production storage.

## Inspect and replay

Consume the events and DLQ topics with the team's standard Kafka tooling. To
replay a reviewed DLQ command, extract its `command` object and publish that
object to the command topic with the original workload key. Do not replay
unreviewed malformed or unauthorized commands.
