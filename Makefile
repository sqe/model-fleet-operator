.PHONY: bootstrap docs-bootstrap docs-build permissions-render test lint helm-lint terraform-validate go-test proto-lint validate image model-image model-deploy artifacts-install artifacts-status artifacts-uninstall kind-up kind-down kind-status

PYTHON ?= python3
VENV := .venv
BIN := $(VENV)/bin
KIND_TF := infra/terraform/kind

bootstrap:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install -e '.[dev]'

docs-bootstrap:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install -e '.[dev,docs]'

docs-build:
	$(BIN)/sphinx-build -W -b html docs docs/_build/html

permissions-render:
	@test -n "$(PERMISSION_CONFIG)" || (echo "PERMISSION_CONFIG is required"; exit 2)
	$(BIN)/python scripts/render_permissions.py --config $(PERMISSION_CONFIG) --output $${PERMISSION_OUTPUT:-.generated-permissions}

test:
	$(BIN)/pytest

lint:
	$(BIN)/ruff check src tests scripts
	$(BIN)/ruff format --check src tests scripts

helm-lint:
	@for profile in kind microk8s aws gcp; do \
		helm lint charts/model-fleet-operator --set profile=$$profile; \
	done
	helm lint charts/aws-karpenter-capacity \
		--set clusterName=test,discoveryTag=test,nodeRoleName=test
	helm template model-fleet charts/model-fleet-operator \
		--set profile=kind,kafka.enabled=true,kafka.bootstrapServers=kafka:9092 \
		--set controlPlane.enabled=true,controlPlane.existingSecret=control-secret \
		--set controlPlane.supervisorRoute.enabled=true,controlPlane.supervisorRoute.hostname=supervisor.example.test \
		--set controlPlane.gatewayRoute.enabled=true,controlPlane.gatewayRoute.hostname=gateway.example.test \
		--set controlPlane.fleetAgentRoute.enabled=true,controlPlane.fleetAgentRoute.hostname=agent.example.test \
		>/dev/null
	helm template model-fleet charts/model-fleet-operator \
		--values examples/slack-operator-values.yaml >/dev/null
	helm repo add cilium https://helm.cilium.io --force-update
	helm template cilium cilium/cilium --namespace kube-system --version 1.20.1 \
		--values infra/cilium/hubble-values.yaml >/dev/null

terraform-validate:
	terraform fmt -check -recursive infra/terraform
	terraform -chdir=infra/terraform/addons init -backend=false
	terraform -chdir=infra/terraform/addons validate
	terraform -chdir=infra/terraform/aws-karpenter init -backend=false
	terraform -chdir=infra/terraform/aws-karpenter validate
	terraform -chdir=$(KIND_TF) init -backend=false
	terraform -chdir=$(KIND_TF) validate

go-test:
	cd sdk/go && go test ./...

proto-lint:
	buf lint
	buf generate

validate: lint test docs-build helm-lint terraform-validate

image:
	docker build -t model-fleet-operator:dev .

model-image:
	@test -n "$(MODEL_CONTEXT)" || (echo "MODEL_CONTEXT is required"; exit 2)
	@test -n "$(MODEL_IMAGE)" || (echo "MODEL_IMAGE is required"; exit 2)
	scripts/build_model_image.sh --context "$(MODEL_CONTEXT)" --image "$(MODEL_IMAGE)" $${MODEL_OUTPUT:---oci dist/model-image.oci.tar}

model-deploy:
	@test -n "$(MODEL_DEPLOY_ARGS)" || (echo "MODEL_DEPLOY_ARGS is required; deployment prints a dry run unless --apply is included"; exit 2)
	scripts/deploy_inference_service.py $(MODEL_DEPLOY_ARGS)

artifacts-install:
	infra/artifacts/artifacts.sh install

artifacts-status:
	infra/artifacts/artifacts.sh status

artifacts-uninstall:
	infra/artifacts/artifacts.sh uninstall

kind-up:
	terraform -chdir=$(KIND_TF) init
	terraform -chdir=$(KIND_TF) apply

kind-down:
	terraform -chdir=$(KIND_TF) destroy

kind-status:
	kubectl --context kind-model-fleet get nodes
	kubectl --context kind-model-fleet get pods -A
	kubectl --context kind-model-fleet get gateway,httproute -A
	kubectl --context kind-model-fleet -n kube-system get deployment/hubble-relay deployment/hubble-ui service/hubble-metrics
