---
title: Routing strategies
description: Single-shot vs sequential, optimal stopping
---

# Routing Strategies: Snap vs. Ladder (`routing.md`)

RealityRouter offers two distinct strategies to optimize model selection: **Snap** (single-shot Expected Utility) and **Ladder** (sequential assessment/escalation). Both strategies are built upon the same Expected Utility mathematics but apply different request lifecycles.

---

## 1. Snap: Single-Shot Expected-Utility Routing (`expected_utility`)

The default operational mode. The router evaluates the utility equations for all configured models simultaneously and routes the query to the single most optimal candidate.

- **Public/Internal Name**: **Snap** / `expected_utility`
- **Best For**: Low latency requirements, interactive coding sessions, and mixed difficulty workloads.
- **Decision Engine**: Immediately selects the candidate `m_i` that maximizes `EU(m_i) = p_i · R − α · c_i − β · t_i`.
- **Parallel Evaluation**: RealityRouter computes the expected utility for all candidates in parallel using light statistical classifiers and regional latency caches. It **does not** execute parallel LLM calls, which would multiply costs and latency.
- **Downstream Call Count**: 
  - *Normal Path*: Exactly one LLM call.
  - *Validation Failure*: If the chosen model's output fails quality or protocol validation, RealityRouter triggers a sequential fallback attempt.

---

## 2. Ladder: Sequential Assessment / Escalation (`tiered_assessment`)

A multi-stage escalation approach. The router begins with the cheapest viable model, evaluates the response, and decides whether the output is statistically sufficient or if it must escalate to a more powerful model.

- **Public/Internal Name**: **Ladder** / `tiered_assessment`
- **Best For**: Maximizing cost savings in autonomous agentic loops where a high proportion of queries are routine and only a few require deep reasoning.
- **Downstream Call Count**: Usually one. Up to three sequential attempts on complex tasks.

### The Ladder Escalation Lifecycle

1. **First Attempt**: Dispatch the query to the cheapest available candidate model in the pool.
2. **Post-Response Evaluation**: Once the completion is received, extract high-level feature metrics from the response text (confidence scores, syntax structure, presence of errors).
3. **Calibrate Probability**: Compute a calibrated probability `p_actual` representing: *"how likely is this generated response correct, given the features of the output we just received?"*
4. **Optimal Stopping & the `p_next = 1.0` Assumption**:
   - The stopping algorithm compares the utility of returning the current answer against the potential utility of escalating to a more powerful "gold-standard" fallback model.
   - **The `p_next = 1.0` Assumption**: In calculating the expected utility of the escalation path, the algorithm optimistically assumes the next-tier flagship model will succeed with 100% probability (`p_next = 1.0`). 
   - **Why this is used**: This acts as an upper-bound counterfactual. It ensures that the router will only stop if the current cheap answer is exceptionally strong, or if the cost of the flagship is too prohibitive under your cost sensitivity `α`.
5. **Escalate Only When Justified**: The router will only execute a sequential attempt if the expected utility margin of the flagship model exceeds the current response's quality score. Otherwise, it stops and returns the cheap response.

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

You can change strategy at any time by running `reality-router setup` or specifying it directly on startup (e.g., `reality-router start --strategy expected_utility`).
