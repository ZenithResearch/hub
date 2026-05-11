# On‑Prem (Self‑Hosted) — Gateway + Runtime + Tool Sandbox

This repo’s runtime split maps cleanly to on‑prem deployments:

- **Edge/WAF/Ingress** → `gateway-http` (public)
- `runtime-grpc` + `tool-sandbox` stay **internal-only**
- Service discovery via **cluster DNS**
- Strict east/west controls via **NetworkPolicies** (Kubernetes) or firewall rules (VMs)

---

## Option A (recommended): Kubernetes

### Prereqs

- Kubernetes cluster (any distro)
- An Ingress controller (e.g., NGINX Ingress)
- A WAF option (one of):
  - NGINX Ingress + ModSecurity/OWASP CRS
  - An external WAF appliance/proxy in front of the Ingress
- A Qdrant endpoint reachable from the cluster (Qdrant Cloud or self-hosted)

### Deploy

1) Build/push the container image to a registry your cluster can pull from.

2) Edit the manifest:

- Set the container image (`<YOUR_IMAGE>`)
- Set `QDRANT_URL` (and optionally `QDRANT_API_KEY`)
- Set the Ingress host (`api.example.com`)

3) Apply:

```bash
kubectl apply -f infra/onprem/k8s/agent-platform.yaml
```

### WebSockets/SSE timeouts

Ensure your Ingress controller timeouts exceed your heartbeat interval.
For NGINX Ingress, the manifest sets:
- `proxy-read-timeout: 3600`
- `proxy-send-timeout: 3600`
- `proxy-buffering: off` (important for SSE)

---

## Option B: VMs / bare metal (reverse proxy)

Equivalent pattern:

```text
WAF/ReverseProxy (nginx/HAProxy) -> gateway-http (public)
runtime-grpc + tool-sandbox      -> private network only
qdrant                           -> private or managed
```

Key configuration points:
- Raise proxy/LB idle timeouts for long-lived connections
- Use WebSocket ping/pong or SSE heartbeats
- Restrict firewall rules:
  - public -> gateway only
  - gateway -> runtime only
  - runtime -> tool-sandbox only

