# Go agent SDK

The Go package implements the same Agent Card, JSON-RPC, health, Prometheus,
manual-commit worker, and result contracts as `modelfleet.agent_runtime` in
Python. Its `Transport` interface keeps the SDK independent of a specific Kafka
client. An adapter must publish with `acks=all` and invoke `Commit` only after
`Publish` succeeds.

```go
metrics := &agent.Metrics{}
http.ListenAndServe(":8080", agent.HTTPHandler(card, metrics))
worker := &agent.Worker{Card: card, Metrics: metrics, Transport: kafka, Handlers: handlers}
go worker.Run(ctx)
```

Use `agent.WorkerHTTPHandler(worker, apiKey)` instead when the service should
also accept synchronous JSON-RPC tasks at `/v1/tasks:execute`. The matching gRPC
types and service stubs are generated from `proto/modelfleet/agent/v1/agent.proto`
with `buf generate`.
