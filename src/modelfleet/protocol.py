"""Typed wire contracts shared by the agent control-plane services."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Skill(BaseModel):
    id: str
    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)


class AgentTransport(BaseModel):
    protocol: Literal["kafka", "http", "grpc"]
    endpoint: str = Field(min_length=1)


class AgentCard(BaseModel):
    name: str
    description: str
    version: str
    endpoint: str
    kafka_topic: str
    kafka_result_topic: str
    max_concurrent_tasks: int = Field(gt=0)
    timeout_seconds: int = Field(gt=0)
    skills: list[Skill]
    transports: list[AgentTransport] = Field(default_factory=list)


class JsonRpcTask(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str
    method: Literal["tasks.execute"] = "tasks.execute"
    params: dict[str, Any]


class JsonRpcResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    jsonrpc: Literal["2.0"] = "2.0"
    id: str
    result: Any | None = None
    error: dict[str, Any] | None = None


class TaskSubmission(BaseModel):
    user_id: str
    prompt: str
    context: dict[str, Any] | None = None
    skill: str | None = None
