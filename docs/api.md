---
title: API reference
description: Endpoints, request/response shapes, headers
---

# API reference

RealityRouter exposes standard OpenAI-compatible endpoints. Any client built for the OpenAI API works — just change the base URL.

## Base URL

With the router running locally, point your client at:

```bash
http://localhost:8000/v1
```

> [!NOTE]
> **API key.** Your client must send an `Authorization` header, but the value isn't validated. The router authenticates to upstream providers using keys in `~/.reality_router/.env`.

## POST /v1/chat/completions

Chat-based interactions. Same request shape as OpenAI:

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer anything" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [
      {"role": "user", "content": "Refactor this function..."}
    ],
    "tools": [...],
    "temperature": 0.2
  }'
```

### The `model` parameter

- `"auto"` — let the router decide per-query. Recommended.
- `"gpt-5.4-thinking"`, `"claude-opus-4"`, etc. — pin to a specific model. Useful for debugging or when you have hard constraints.
- `"tier:cheap"`, `"tier:flagship"` — bias the router toward a price tier without naming a specific model.

### Response shape

Same as OpenAI's chat.completion response, with an additional `x-rr-*` set of headers exposing the routing decision:

- `X-RR-Model` — which model handled the request.
- `X-RR-Strategy` — `single-shot` or `sequential`.
- `X-RR-Attempts` — how many models were tried before success.
- `X-RR-Cost` — estimated USD cost of this request.
- `X-RR-Saved` — estimated USD saved vs the always-flagship counterfactual.

## POST /v1/completions

Legacy text completion. Same shape and headers as the OpenAI equivalent. Generally prefer `/v1/chat/completions` for new code.

## GET /.well-known/agent-card.json

Standard agent-discovery endpoint for A2A communication. Returns the router's name, version, capabilities, and supported protocols.

```bash
curl http://localhost:8000/.well-known/agent-card.json
```

## GET /health

Liveness check. Returns `200 OK` with a small JSON body when the router is up:

```json
{
  "status": "ok",
  "uptime_s": 3812,
  "models_active": 7,
  "models_quarantined": 1
}
```

## GET /metrics/dashboard

The web dashboard UI. Renders the live view of spend, calibration, and per-agent activity. See the [Dashboard](./dashboard.md) guide.

## Error codes

- `200` — success. Response body matches OpenAI's shape.
- `400` — malformed request (invalid JSON, missing required fields).
- `429` — concurrency limit exceeded on all eligible models. Retry after a moment.
- `502 Bad Gateway` — infrastructure failure with the upstream provider (timeout, 500, invalid key). Inspect `X-RR-Upstream-Error` for details.
- `503` — all configured models are quarantined by the circuit breaker. Wait for recovery or check provider status.

> [!INFO]
> **Quality failures don't return errors.** If a model returns broken JSON, truncated output, or an AI refusal, the router treats that as a quality failure and **silently escalates** to a better model. Your client only sees the validated response.
