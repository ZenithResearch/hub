output "matrix_url" {
  description = "HTTPS-only public Matrix endpoint."
  value       = "https://${var.matrix_server_name}"
}

output "elastic_ip" {
  description = "Elastic IP to use for the hostname A record; null before runtime is enabled."
  value       = try(aws_eip.matrix[0].public_ip, null)
}

output "instance_id" {
  description = "SSM-managed EC2 instance ID; null before runtime is enabled."
  value       = try(aws_instance.matrix[0].id, null)
}

output "secret_arn" {
  description = "Secret to populate out of band before enabling the runtime."
  value       = aws_secretsmanager_secret.matrix.arn
}
