"""Failure-tolerant, metadata-only MLflow tracing."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from time import monotonic
from typing import Any


class TaskTracer:
    def __init__(self, tracking_uri: str | None = None) -> None:
        self.tracking_uri = (
            tracking_uri if tracking_uri is not None else os.getenv("MLFLOW_TRACKING_URI")
        )

    @contextmanager
    def trace(self, task_id: str, agent: str, skill: str) -> Iterator[dict[str, Any]]:
        values: dict[str, Any] = {"status": "ok"}
        started = monotonic()
        try:
            yield values
        except Exception:
            values["status"] = "error"
            raise
        finally:
            if self.tracking_uri:
                self._log(task_id, agent, skill, monotonic() - started, values)

    def _log(
        self, task_id: str, agent: str, skill: str, latency: float, values: dict[str, Any]
    ) -> None:
        try:
            import mlflow

            mlflow.set_tracking_uri(self.tracking_uri)
            with mlflow.start_run(run_name=f"task-{task_id}"):
                mlflow.log_params({"task_id": task_id, "agent": agent, "skill": skill})
                mlflow.log_metrics(
                    {
                        "latency_seconds": latency,
                        **{
                            key: float(values[key])
                            for key in ("input_tokens", "output_tokens", "cost_usd")
                            if key in values
                        },
                    }
                )
                mlflow.set_tag("status", str(values["status"]))
        except Exception:
            # Telemetry must never affect task delivery.
            return
