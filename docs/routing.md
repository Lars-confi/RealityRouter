---
title: Routing strategies
description: Single-shot vs sequential, optimal stopping
---

# Routing strategies

Two ways for the router to pick a model: score them all upfront and send to the winner (single-shot), or start cheap and escalate only if the output fails validation (sequential). Both run the same Expected Utility math.

## Expected Utility (single-shot)

The default mode. The router evaluates every configured model in parallel and routes the query to the single best candidate.

- **Best for** — balanced workloads, low latency, mixed query types.
- **Logic** — immediately selects the `m_i` that maximizes `p_i · R − α · c_i − β · t_i`.
- **Network calls** — exactly one LLM call per request.

If the chosen model's output fails quality validation (truncation, broken JSON, refusal), the router still escalates to a fallback — but the default path is one shot, one model.

## Tiered Assessment (sequential)

A multi-stage approach. The router starts with the cheapest viable model, validates the output, then decides whether the answer is good enough or whether to escalate.

- **Best for** — maximum cost optimization, autonomous agentic workflows where many queries are easy and a few are hard.
- **Network calls** — usually one. Up to three on hard queries.

### How it works

For each request:

1. **First attempt** — start with the cheapest available model.
2. **Post-response calibration** — once the response arrives, re-run feature extraction with diagnostic metrics appended: confidence scores, response entropy, and logprobs (if the model exposes them).
3. **Validation** — compute a calibrated probability `p_actual` using the full feature set. This is "how likely is this answer correct, given the response we just got?"
4. **Optimal stopping** — compare the utility of the current answer against the potential utility of escalating to a more powerful model (assuming `p_next = 1.0` for the gold-standard fallback).
5. **Escalate only if worth it** — the router escalates only when the potential gain in accuracy outweighs the additional cost and latency.

> [!INFO]
> **Why sequential saves money.** Most queries to a coding agent are routine — a small refactor, a quick lookup, a formatting fix. A cheap or local model nails them on the first try. The router never escalates. You pay zero or near-zero per call. Only the hard queries — the ones that genuinely need Opus or GPT-5.4 Thinking — pay the flagship price.

## Circuit breaker

Independent of which strategy you pick, the router runs a circuit breaker on every model:

- **Automatic failure detection** — repeated timeouts, 500 errors, or invalid responses trip the circuit.
- **Temporary isolation** — a tripped model is bypassed for subsequent requests. Routing falls back to the next best candidate.
- **Graceful recovery** — after a configurable timeout, the circuit goes half-open and lets a few test requests through. If they succeed, the circuit closes and traffic resumes normally.

## Dynamic capability discovery

On startup, the router probes newly discovered models in the background to determine their true capabilities:

- Does the model support `tool_calling` (function calling)?
- Does the model expose `logprobs` for advanced confidence scoring?

These probes are out-of-band and don't pollute your historical metrics. Tool-intensive requests are only routed to capable models. Models without native function-calling support fall through the MCP/ACP translation layer.

## Concurrency limits

To prevent overwhelming specific providers — especially local instances like Ollama — you can cap parallel requests per model in `~/.reality_router/user_models.json`:

```json
{
  "qwen3-coder:30b": {
    "thread_limit": 2
  },
  "gpt-5.4-thinking": {
    "thread_limit": 10
  }
}
```

The router maintains an internal semaphore per model. If a model is at its limit, the router **automatically skips** it and routes to the next-best candidate. No stalls, no errors.

## Which strategy should I pick?

- **Pick single-shot** if latency matters, your queries vary in difficulty, or your workload is mostly interactive (humans waiting on responses).
- **Pick sequential** if you're running autonomous agents (RooCode, OpenClaw, AutoGPT), have lots of routine queries mixed with occasional hard ones, and care primarily about minimizing cost.

You can change strategy at any time by re-running `./start.sh`.
