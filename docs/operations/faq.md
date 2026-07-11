# Operations FAQ

## If PR #86 is merged, can Matrix Synapse be deployed to production?

Yes, but merge does not deploy Synapse automatically. The hardened image workflow builds, verifies, scans, and publishes an immutable ECR candidate; it does not run Terraform apply.

Production deployment is a reviewed two-phase manual Terraform operation using `scripts/prod_terraform_cd.sh` or equivalent explicit Terraform commands:

1. Phase 1 provisions the inactive Synapse infrastructure with `enable_matrix_synapse=true`, `enable_matrix_backup=true`, and `start_matrix_synapse_service=false`.
2. The operator confirms the SNS email subscription, adds external ACM validation and ALB DNS records, populates required Secrets Manager versions, and verifies the hardened ECR digest.
3. Phase 2 sets `start_matrix_synapse_service=true` and enables the reviewed HTTPS/federation gates.
4. Issue #67 closes only after public smoke, capacity, federation, backup, and isolated restore evidence pass.

The generic production helper requires current image tags for every existing service so an unrelated service is not rolled backward. Matrix-specific values may be supplied through private production tfvars or `TF_VAR_*` environment variables; no raw secret values belong in committed files or command output.
