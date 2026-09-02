package agent

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestDecodeTask(t *testing.T) {
	task, err := DecodeTask([]byte(`{"jsonrpc":"2.0","method":"tasks.execute","id":"one","params":{"skill":"echo"}}`))
	if err != nil || task.ID != "one" {
		t.Fatalf("unexpected task: %#v, %v", task, err)
	}
}

func TestMetrics(t *testing.T) {
	metrics := &Metrics{}
	metrics.begin()
	metrics.observe(false, 0)
	if !strings.Contains(metrics.Prometheus("echo"), `status="completed"} 1`) {
		t.Fatal("completion metric missing")
	}
}

func TestWorkerHTTPHandlerRequiresBearerToken(t *testing.T) {
	worker := &Worker{
		Card: Card{Name: "echo", Version: "1"},
		Handlers: map[string]Handler{
			"echo": func(_ context.Context, params map[string]any) (any, error) {
				return params["prompt"], nil
			},
	}
	handler := WorkerHTTPHandler(worker, "secret")
	payload := `{"jsonrpc":"2.0","method":"tasks.execute","id":"one","params":{"skill":"echo","prompt":"hello"}}`

	unauthorized := httptest.NewRequest(http.MethodPost, "/v1/tasks:execute", strings.NewReader(payload))
	unauthorizedResult := httptest.NewRecorder()
	handler.ServeHTTP(unauthorizedResult, unauthorized)
	if unauthorizedResult.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401, got %d", unauthorizedResult.Code)
	}

	request := httptest.NewRequest(http.MethodPost, "/v1/tasks:execute", strings.NewReader(payload))
	request.Header.Set("Authorization", "Bearer secret")
	result := httptest.NewRecorder()
	handler.ServeHTTP(result, request)
	if !strings.Contains(result.Body.String(), `"result":"hello"`) {
		t.Fatalf("unexpected result: %s", result.Body.String())
	}
}
