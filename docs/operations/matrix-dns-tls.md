# Matrix DNS/TLS operator contract

ISS-P14-003 records the DNS and TLS contract for `synapse.zenith-research.ca`.

## Locked v0 contract

- Direct Matrix server/client host: `synapse.zenith-research.ca`.
- DNS owner: Route53 zone identified by `matrix_hosted_zone_id`.
- TLS owner: ACM certificate `aws_acm_certificate.matrix`, validated by DNS.
- Client API: HTTPS on port 443.
- Federation: HTTPS on port 8448, intentionally enabled only when `enable_matrix_federation = true`.

## Smoke commands

```bash
dig +short synapse.zenith-research.ca
openssl s_client -connect synapse.zenith-research.ca:443 -servername synapse.zenith-research.ca </dev/null
openssl s_client -connect synapse.zenith-research.ca:8448 -servername synapse.zenith-research.ca </dev/null
curl -fsS https://synapse.zenith-research.ca/_matrix/client/versions
```

## Forbidden claims

Do not claim production Synapse is deployed, durable, or appservice-ready from this DNS/TLS contract alone. Production readiness still requires P14 backup/restore and P15 appservice `whoami` plus delivery smoke.
