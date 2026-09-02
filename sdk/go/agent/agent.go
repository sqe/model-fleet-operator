// Package agent implements Model Fleet's language-neutral agent contract.
package agent

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
	"time"
)

type Skill struct {
	ID          string         `json:"id"`
	Name        string         `json:"name"`
	Description string         `json:"description"`
	InputSchema map[string]any `json:"input_schema"`
}

type TransportDescriptor struct {
	Protocol string `json:"protocol"`
	Endpoint string `json:"endpoint"`
}

type Card struct {
	Name               string                `json:"name"`
	Description        string                `json:"description"`
	Version            string                `json:"version"`
	Endpoint           string                `json:"endpoint"`
	KafkaTopic         string                `json:"kafka_topic"`
	KafkaResultTopic   string                `json:"kafka_result_topic"`
	MaxConcurrentTasks int                   `json:"max_concurrent_tasks"`
	TimeoutSeconds     int                   `json:"timeout_seconds"`
	Skills             []Skill               `json:"skills"`
	Transports         []TransportDescriptor `json:"transports,omitempty"`
}

type Task struct {
	JSONRPC string         `json:"jsonrpc"`
	Method  string         `json:"method"`
	Params  map[string]any `json:"params"`
	ID      string         `json:"id"`
}

type RPCError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

type Result struct {
	JSONRPC string    `json:"jsonrpc"`
	Result  any       `json:"result,omitempty"`
	Error   *RPCError `json:"error,omitempty"`
	ID      string    `json:"id"`
}

func DecodeTask(payload []byte) (Task, error) {
	var task Task
	if err := json.Unmarshal(payload, &task); err != nil {
		return task, err
	}
	if task.JSONRPC != "2.0" || task.Method != "tasks.execute" || task.ID == "" {
		return task, errors.New("invalid Model Fleet JSON-RPC task")
	}
	return task, nil
}

type Metrics struct {
	mu        sync.Mutex
	active    int
	completed uint64
	failed    uint64
	duration  float64
}

func (m *Metrics) observe(failed bool, duration time.Duration) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.active--
	if failed {
		m.failed++
	} else {
		m.completed++
	}
	m.duration += duration.Seconds()
}

func (m *Metrics) begin() { m.mu.Lock(); m.active++; m.mu.Unlock() }

func (m *Metrics) Prometheus(agentName string) string {
	m.mu.Lock()
	defer m.mu.Unlock()
	name := prometheusLabel(agentName)
	return fmt.Sprintf(
		"model_fleet_agent_active_tasks{agent=\"%s\"} %d\n"+
			"model_fleet_agent_tasks_total{agent=\"%s\",status=\"completed\"} %d\n"+
			"model_fleet_agent_tasks_total{agent=\"%s\",status=\"failed\"} %d\n"+
			"model_fleet_agent_task_duration_seconds_sum{agent=\"%s\"} %g\n",
		name, m.active, name, m.completed, name, m.failed, name, m.duration,
	)
}

func prometheusLabel(value string) string {
	value = strings.ReplaceAll(value, `\`, `\\`)
	value = strings.ReplaceAll(value, "\n", `\n`)
	return strings.ReplaceAll(value, `"`, `\"`)
}

func HTTPHandler(card Card, metrics *Metrics) http.Handler {
	if metrics == nil {
		metrics = &Metrics{}
	}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, map[string]string{"status": "healthy", "agent": card.Name, "version": card.Version})
	})
	mux.HandleFunc("GET /.well-known/agent.json", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, card)
	})
	mux.HandleFunc("GET /metrics", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/plain; version=0.0.4")
		_, _ = w.Write([]byte(metrics.Prometheus(card.Name)))
	})
	return mux
}

// WorkerHTTPHandler exposes discovery and authenticated synchronous task execution.
func WorkerHTTPHandler(worker *Worker, apiKey string) http.Handler {
	mux := http.NewServeMux()
	mux.Handle("/", HTTPHandler(worker.Card, worker.metrics()))
	mux.HandleFunc("POST /v1/tasks:execute", func(w http.ResponseWriter, request *http.Request) {
		if apiKey != "" && !validBearer(request.Header.Get("Authorization"), apiKey) {
			w.WriteHeader(http.StatusUnauthorized)
			writeJSON(w, map[string]string{"error": "invalid bearer token"})
			return
		}
		payload, err := io.ReadAll(http.MaxBytesReader(w, request.Body, 1<<20))
		if err != nil {
			w.WriteHeader(http.StatusRequestEntityTooLarge)
			writeJSON(w, map[string]string{"error": "invalid task body"})
			return
		}
		writeJSON(w, worker.process(request.Context(), payload))
	})
	return mux
}

func validBearer(authorization, expected string) bool {
	supplied := strings.TrimPrefix(authorization, "Bearer ")
	if !strings.HasPrefix(authorization, "Bearer ") || len(supplied) != len(expected) {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(supplied), []byte(expected)) == 1
}

func writeJSON(w http.ResponseWriter, value any) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(value)
}

type Message struct {
	Value  []byte
	Commit func(context.Context) error
}

type Transport interface {
	Receive(context.Context, string) (Message, error)
	Publish(context.Context, string, string, []byte) error
}

type Handler func(context.Context, map[string]any) (any, error)

type Worker struct {
	Card       Card
	Handlers   map[string]Handler
	Transport  Transport
	Metrics    *Metrics
}

func (w *Worker) metrics() *Metrics {
	if w.Metrics == nil {
		w.Metrics = &Metrics{}
	}
	return w.Metrics
}

func (w *Worker) Run(ctx context.Context) error {
	for {
		message, err := w.Transport.Receive(ctx, w.Card.KafkaTopic)
		if err != nil {
			return err
		}
		result := w.process(ctx, message.Value)
		payload, err := json.Marshal(result)
		if err != nil {
			return err
		}
		if err := w.Transport.Publish(ctx, w.Card.KafkaResultTopic, result.ID, payload); err != nil {
			return err
		}
		if err := message.Commit(ctx); err != nil {
			return err
		}
	}
}

func (w *Worker) process(ctx context.Context, payload []byte) Result {
	task, err := DecodeTask(payload)
	if err != nil {
		return Result{JSONRPC: "2.0", ID: task.ID, Error: &RPCError{Code: -32600, Message: err.Error()}}
	}
	skill, _ := task.Params["skill"].(string)
	handler, ok := w.Handlers[skill]
	if !ok {
		return Result{JSONRPC: "2.0", ID: task.ID, Error: &RPCError{Code: -32601, Message: "unknown skill"}}
	}
	started := time.Now()
	w.metrics().begin()
	value, err := handler(ctx, task.Params)
	w.Metrics.observe(err != nil, time.Since(started))
	if err != nil {
		return Result{JSONRPC: "2.0", ID: task.ID, Error: &RPCError{Code: -32000, Message: err.Error()}}
	}
	return Result{JSONRPC: "2.0", ID: task.ID, Result: value}
}
