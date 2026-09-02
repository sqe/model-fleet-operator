"""Prometheus metrics shared by Model Fleet processes."""

from prometheus_client import Counter, Gauge

RECONCILES = Counter(
    "model_fleet_reconciliations_total",
    "Operator reconciliations",
    ["kind", "result"],
)
INFERENCE_READY = Gauge(
    "model_fleet_inference_ready_replicas",
    "Ready inference replicas",
    ["namespace", "workload"],
)
TRAINING_PHASE = Gauge(
    "model_fleet_training_phase",
    "Training phase as a one-hot gauge",
    ["namespace", "workload", "phase"],
)
COMMANDS = Counter(
    "model_fleet_control_commands_total",
    "Control commands handled",
    ["result"],
)
