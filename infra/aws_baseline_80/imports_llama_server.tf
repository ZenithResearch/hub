# C3 live adoption imports for the production internal llama-server.
# These bind manually created production resources into Terraform state during the
# first C3 apply. Remove in a follow-up cleanup commit after import has landed.

import {
  to = aws_security_group.llama_server
  id = "sg-08ed2c67b3bbdb33a"
}

import {
  to = aws_service_discovery_service.llama_server
  id = "srv-q3t5qcj7hspw42qy"
}

import {
  to = aws_iam_role.llama_server_task
  id = "zenith-hub-prod-llama-server-task-role"
}

import {
  to = aws_iam_role_policy.llama_server_model_efs
  id = "zenith-hub-prod-llama-server-task-role:llama-server-model-efs"
}

import {
  to = aws_cloudwatch_log_group.llama_server
  id = "/ecs/zenith-hub-prod/llama-server"
}

import {
  to = aws_ecs_task_definition.llama_server
  id = "arn:aws:ecs:us-east-1:044528206149:task-definition/zenith-hub-prod-llama-server:1"
}

import {
  to = aws_ecs_service.llama_server
  id = "zenith-hub-prod-cluster/zenith-hub-prod-llama-server"
}
