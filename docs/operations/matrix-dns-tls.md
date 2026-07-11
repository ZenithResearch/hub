# Matrix DNS/TLS operator contract

ISS-P14-003 records the DNS and TLS contract for `synapse.zenith-research.ca`.

## Locked v0 contract

- Direct Matrix server/client host: `synapse.zenith-research.ca`.
- DNS owner: the external authoritative provider for `zenith-research.ca` (currently not Route53). Leave `matrix_hosted_zone_id` empty; use `matrix_certificate_dns_validation_records` for the ACM validation CNAME and `matrix_alb_dns_name` for the public host record. Route53 remains supported when a hosted-zone ID is explicitly supplied.
- TLS owner: ACM certificate `aws_acm_certificate.matrix`, validated by external DNS before `enable_matrix_https_listener` or federation is enabled. The certificate is attached to the existing Hub HTTPS ALB listener as an SNI certificate; external DNS does not block listener creation once ACM reports `ISSUED`.
- Client API: HTTPS on port 443 via `aws_lb_listener_rule.matrix_https_host` host-header routing on the existing `aws_lb_listener.https`; this contract must not create a second ALB listener on port 443.
- Federation: HTTPS on port 8448, intentionally enabled only when `enable_matrix_federation = true`; the ALB security group opens 8448 and forwards to the Matrix client target group on 8008 only under that explicit gate.
- Target readiness: issue #67 / PR #86 adds ECS-managed IP target registration. `enable_matrix_synapse` creates the target infrastructure while `start_matrix_synapse_service` remains false until required secret versions and external DNS/TLS are ready.

## Smoke commands

```bash
dig +short synapse.zenith-research.ca
openssl s_client -connect synapse.zenith-research.ca:443 -servername synapse.zenith-research.ca </dev/null
openssl s_client -connect synapse.zenith-research.ca:8448 -servername synapse.zenith-research.ca </dev/null
curl -fsS https://synapse.zenith-research.ca/_matrix/client/versions
```

## Forbidden claims

Do not claim production Synapse is deployed, durable, or appservice-ready from this DNS/TLS contract alone. Production readiness still requires P14 backup/restore and P15 appservice `whoami` plus delivery smoke.
