# ISS-P14-002: Core Matrix EC2 + encrypted EBS adoption
# Ported/adapted per vault spec iss-p14-002
# Locked: EC2 + encrypted EBS for v0, synapse.zenith-research.ca, federation 8448 enabled
resource "aws_instance" "matrix" {
  # stub for adoption; full impl in later tasks
  ami           = var.matrix_ami
  instance_type = var.matrix_instance_type
  # TODO: EBS encryption, user_data from matrix_user_data.sh.tpl
}

# Edge cases for iss-p14-002:
# - no raw secrets in tf
# - idempotent apply
# - no production claim without evidence
# - federation 8448 opt-in only


# Operator evidence for iss-p14-002:
# Verification: terraform fmt -check; terraform init/validate; terraform plan -var=enable_matrix=true
# Pricing evidence and TLS tradeoff recorded in vault capture before apply
# Source: docs/issues/matrix-synapse-v0/iss-p14-002-core-terraform-resource-adoption.md

