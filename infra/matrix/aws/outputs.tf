output "instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.matrix.id
}

output "elastic_ip" {
  description = "Elastic IP — point your DNS A record here"
  value       = aws_eip.matrix.public_ip
}

output "matrix_client_url" {
  description = "Matrix client API endpoint"
  value       = "http://${aws_eip.matrix.public_ip}:8008"
}

output "matrix_federation_url" {
  description = "Matrix federation endpoint (point _matrix._tcp SRV record here)"
  value       = "http://${aws_eip.matrix.public_ip}:8448"
}

output "security_group_id" {
  description = "Security group ID"
  value       = aws_security_group.matrix.id
}

output "ebs_volume_id" {
  description = "Data EBS volume ID (Postgres + media store)"
  value       = aws_ebs_volume.matrix_data.id
}
