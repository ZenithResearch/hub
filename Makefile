.PHONY: start up seed down proto test doctor aws-edge-backend aws-edge-apply aws-edge-push aws-edge-secret aws-edge-seed aws-edge-up

-include .env
-include .env.local

AWS_PROFILE ?=
export AWS_PROFILE

AWS_REGION ?= us-west-2
STATE_BUCKET ?=
LOCK_TABLE ?=
TF_STATE_KEY ?= agent-platform/aws-edge/terraform.tfstate

REPO_NAME ?= agent-platform
IMAGE_TAG ?= latest

DOCKER_DEFAULT_PLATFORM ?=

QDRANT_URL ?=
QDRANT_SECRET_NAME ?= agent-platform/qdrant_api_key

start:
	bash scripts/start.sh

up:
	docker compose up --build

down:
	docker compose down -v

seed:
	docker compose run --rm kb-indexer

proto:
	python -m grpc_tools.protoc -I./proto --python_out=./libs/common/proto --grpc_python_out=./libs/common/proto ./proto/agent.proto ./proto/agent_admin.proto
	python -c "import pathlib,re; files=pathlib.Path('libs/common/proto').glob('*_pb2_grpc.py'); [(lambda p: p.write_text(re.sub(r'^import (\\w+_pb2) as ', r'from . import \\1 as ', p.read_text(), flags=re.MULTILINE)))(p) for p in files]"

test:
	docker compose run --rm -e REVIEWS_DATA_DIR=/tmp/hub-reviews -e CLIENTS_DB_PATH=/tmp/hub-clients.db runtime-grpc python -c "import libs.common.config, libs.kb.qdrant_store, libs.tools.registry; import services.runtime_grpc.main, services.tool_sandbox.main, services.gateway_http.app; print('imports_ok')"

doctor:
	bash scripts/doctor.sh

aws-edge-backend:
	AWS_REGION="$(AWS_REGION)" STATE_BUCKET="$(STATE_BUCKET)" LOCK_TABLE="$(LOCK_TABLE)" bash scripts/aws_backend_bootstrap.sh

aws-edge-push:
	AWS_REGION="$(AWS_REGION)" REPO_NAME="$(REPO_NAME)" IMAGE_TAG="$(IMAGE_TAG)" DOCKER_DEFAULT_PLATFORM="$(DOCKER_DEFAULT_PLATFORM)" bash scripts/aws_ecr_push.sh

aws-edge-secret:
	@if [ -z "$(QDRANT_API_KEY)" ]; then \
	  echo "QDRANT_API_KEY not set; skipping Secrets Manager update"; \
	else \
	  AWS_REGION="$(AWS_REGION)" QDRANT_SECRET_NAME="$(QDRANT_SECRET_NAME)" QDRANT_API_KEY="$(QDRANT_API_KEY)" bash scripts/aws_set_qdrant_secret.sh; \
	fi

aws-edge-apply:
	@if [ -z "$(STATE_BUCKET)" ] || [ -z "$(LOCK_TABLE)" ] || [ -z "$(QDRANT_URL)" ]; then \
	  echo "Missing required env vars: STATE_BUCKET, LOCK_TABLE, QDRANT_URL"; \
	  exit 1; \
	fi
	@AWS_ACCOUNT_ID="$$(aws sts get-caller-identity --query Account --output text --region "$(AWS_REGION)")" && \
	IMAGE_URI="$$AWS_ACCOUNT_ID.dkr.ecr.$(AWS_REGION).amazonaws.com/$(REPO_NAME):$(IMAGE_TAG)" && \
	QDRANT_SECRET_ARN="$$(aws secretsmanager describe-secret --secret-id "$(QDRANT_SECRET_NAME)" --query ARN --output text --region "$(AWS_REGION)" 2>/dev/null || true)" && \
	cd infra/aws/terraform && \
	AWS_REGION="$(AWS_REGION)" terraform init \
	  -backend-config="bucket=$(STATE_BUCKET)" \
	  -backend-config="key=$(TF_STATE_KEY)" \
	  -backend-config="region=$(AWS_REGION)" \
	  -backend-config="dynamodb_table=$(LOCK_TABLE)" \
	  -backend-config="encrypt=true" && \
	TF_VAR_aws_region="$(AWS_REGION)" \
	TF_VAR_container_image="$$IMAGE_URI" \
	TF_VAR_qdrant_url="$(QDRANT_URL)" \
	TF_VAR_qdrant_api_key_secret_arn="$$QDRANT_SECRET_ARN" \
	TF_VAR_enable_cloudfront=true \
	TF_VAR_allow_cloudfront_only=true \
	TF_VAR_enable_https=false \
	terraform apply -auto-approve

aws-edge-seed:
	@cd infra/aws/terraform && \
	CLUSTER_NAME="$$(terraform output -raw ecs_cluster_name)" && \
	TASK_DEF_ARN="$$(terraform output -raw kb_indexer_task_definition_arn)" && \
	SG_ID="$$(terraform output -raw kb_indexer_security_group_id)" && \
	SUBNETS="$$(terraform output -json private_subnet_ids | python3 -c 'import json,sys; print(",".join(json.load(sys.stdin)))')" && \
	aws ecs run-task \
	  --region "$(AWS_REGION)" \
	  --cluster "$$CLUSTER_NAME" \
	  --launch-type FARGATE \
	  --task-definition "$$TASK_DEF_ARN" \
	  --network-configuration "awsvpcConfiguration={subnets=[$$SUBNETS],securityGroups=[$$SG_ID],assignPublicIp=DISABLED}"

aws-edge-up: aws-edge-backend aws-edge-push aws-edge-secret aws-edge-apply aws-edge-seed

