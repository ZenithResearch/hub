# Matrix DNS/TLS operator contract

ISS-P14-003 records the DNS and TLS contract for `synapse.zenith-research.ca`.

## Locked v0 contract

- Direct Matrix server/client host: `synapse.zenith-research.ca`.
- DNS owner: Route53 zone identified by `matrix_hosted_zone_id`.
- TLS owner: ACM certificate `aws_acm_certificate.matrix`, validated by DNS and attached to the existing Hub HTTPS ALB listener as an SNI certificate when `matrix_hosted_zone_id`, `public_hub_domain_name`, and `enable_https_listener` are all configured.
- Client API: HTTPS on port 443 via `aws_lb_listener_rule.matrix_https_host` host-header routing on the existing `aws_lb_listener.https`; this contract must not create a second ALB listener on port 443.
- Federation: HTTPS on port 8448, intentionally enabled only when `enable_matrix_federation = true`; the ALB security group opens 8448 and forwards to the Matrix client target group on 8008 only under that explicit gate.
- Target readiness: `aws_lb_target_group.matrix_client` is declared, but no production Synapse target attachment is claimed by this issue. A later P14/P15 step must attach a real target and smoke it before production readiness claims.

## Smoke commands

```bash
dig +short synapse.zenith-research.ca
openssl s_client -connect synapse.zenith-research.ca:443 -servername synapse.zenith-research.ca </dev/null
openssl s_client -connect synapse.zenith-research.ca:8448 -servername synapse.zenith-research.ca </dev/null
curl -fsS https://synapse.zenith-research.ca/_matrix/client/versions
```

## Forbidden claims

Do not claim production Synapse is deployed, durable, or appservice-ready from this DNS/TLS contract alone. Production readiness still requires P14 backup/restore and P15 appservice `whoami` plus delivery smoke.
