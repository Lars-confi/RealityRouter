---
title: How it works
description: Expected Utility, probability updates, sentiment feedback
---

# How it works

RealityRouter scores every model on Expected Utility — a single number that combines accuracy, cost, and latency — and routes to the winner. The probabilities behind that math come from Reality Check™ calibration, updated continuously from real outcomes.

## Expected Utility

For every incoming request, the router computes Expected Utility for each configured model `m_i`:

```text
EU(m_i) = p_i · R − α · c_i − β · t_i
```

The five terms:

- `p_i` **probability of success** — the estimated likelihood this specific model produces a correct/high-quality answer to *this specific kind* of query. Calibrated by Reality Check™ from historical outcomes on similar tasks. Range: 0–1.
- `R` **reward** — the value of a correct answer. You set this once during setup.
- `c_i` **cost** — the estimated dollar cost of running `m_i` on this query. Computed from input token count, historical output length, and the model's specific per-token pricing.
- `t_i` **latency** — the estimated response time, derived from a rolling 5–10 minute average for this model.
- `α` & `β` **sensitivities** — your weights on cost and latency, tuned once during setup. `α` high → router prefers cheap models. `β` high → router prefers fast ones.

### The decision rule

The router selects the model with the highest expected utility:

```text
m* = argmax  ( p_i · R  −  α · c_i  −  β · t_i )
       i∈M
```

That's it. No heuristics, no marketing tiers, no "always use the flagship." Every model — cheap, expensive, local, cloud — competes on the same math.

## Dynamic cost estimation

The cost term `c_i` isn't static — the router tracks per-token pricing for every model and adjusts in real time:

- **Automated Pricing Manager** pulls up-to-date input/output token prices from the LiteLLM open registry weekly. Your utility math always reflects what you actually pay.
- **Manual configuration** via `~/.reality_router/user_models.json` takes priority — useful for custom models, local instances, or negotiated enterprise pricing.
- **Context-aware** — the router tokenizes your query, combines that with each model's historical completion length, and penalizes cost accurately for large context windows (where pricing tiers often kick in).

## How probabilities get smarter

`p_i` is the hard part — and where Reality Check™ does the work. The router doesn't just store a fixed success rate per model. It tracks **per-model, per-task-type** probabilities that update continuously.

### 1. Unified feature extraction

Every request — regardless of strategy — is decomposed into a consistent set of features: AST complexity (for code), task type (refactor / explain / generate / review), trace frequencies, agent fingerprint (Cursor, Zed, Claude Code, etc.), prompt length, and more.

### 2. Reality Check™ calibration

These features are sent to the Reality Check calibration service, which compares the current request against historical outcomes for structurally similar requests. The result: `p_i` for each candidate model.

### 3. Sentiment feedback loop

The router watches the conversation for implicit feedback. If a user follows up with a correction, complaint, or "try again," a small sentiment model flags it as unhappy. The router lowers `p_i` for that model on that task type. Future similar prompts route elsewhere.

> [!NOTE]
> **Sentiment cost.** Sentiment analysis runs a background call to a cheap fast model you pick at setup (recommended: Claude Haiku or Gemini Flash). Adds a few cents per 1,000 requests.

### 4. Continuous learning

All signals — successful completions, quality failures, sentiment, validation errors — are logged and feed back into Reality Check™'s calibration. Tomorrow's routing reflects yesterday's outcomes. Without you ever filling out a survey.

## Why these probabilities can be trusted

The router is only as honest as the probabilities driving it. If `p_i` is wrong, the EU math falls apart — you over-route to bad models and under-route to good ones. So Reality Check™ doesn't just "estimate" probabilities. It uses two families of statistical methods chosen specifically for their mathematical guarantees.

### Venn predictors

Venn predictors produce probabilities that are **calibrated by construction**. Under standard exchangeability assumptions, if a Venn predictor says "70%," outcomes really do occur about 70% of the time — distribution-free, and without training-time tuning to enforce the property. That's a formal guarantee, not an empirical observation.

### Conformal prediction

Conformal prediction produces prediction sets with **provably bounded error**. For any chosen significance level `α`, the set covers the true outcome with probability at least `1 − α` — independent of the underlying distribution. Together with Venn, these two families form the small set of methods with this kind of formal validity result.

### Why not LLM-as-judge or learned calibrators?

The alternatives all introduce new sources of error:

- **LLM-as-judge** — a second LLM grades the first. The judge has its own bias and hallucination rate, layered on top of what you're trying to measure. You don't get one probability; you get one probability plus the judge's error rate.
- **Learned calibrators** — a small model converts raw confidence into probability. Adds another model whose own error you have to track and re-train as distributions shift.
- **Soft voting / ensembles** — combine multiple models' outputs. Works well empirically on some tasks, but lacks formal validity guarantees and can't tell you *how much* to trust a given decision.

Reality Check™'s `p_i` is the most honest estimate you can get without introducing fresh sources of uncertainty. That matters every time the router has to decide whether to ship cheap or escalate to flagship — because it's the trustworthiness of the probability, not just its value, that determines whether the routing decision is correct.

> [!NOTE]
> The details of how Venn and conformal methods are applied internally — feature spaces, taxonomies, the specific calibration set construction — are part of the Reality Check service. The guarantees stated above hold for the published outputs.

## Protocol & quality validation

Before any response is returned to your client, the router inspects the raw output for issues that would break an agent loop:

- **Unclosed Markdown** — broken code blocks (` ``` `)
- **Malformed JSON** — invalid tool calls or JSON data blocks
- **Broken agent tags** — unclosed `<thought>`, `<command>`, etc.
- **"Laziness"** — code that skips with `// ...existing code...`
- **AI refusals** — "As an AI language model…"
- **Heuristic truncation** — abrupt endings mid-word or on conjunctions

If anything trips, the router **silently escalates** to a better model. Negative feedback gets logged to Reality Check™. Your client sees only the clean response.

### Quality vs infrastructure failures

The router distinguishes between two failure modes:

- **Quality failures** (truncation, malformed syntax, refusals) → negative feedback to Reality Check + automatic escalation.
- **Infrastructure failures** (timeouts, API 500s, invalid keys) → **do not** contaminate Reality Check metrics. Instead, the router propagates HTTP 502 so you can fix what's actually broken.

## Next

- [Routing strategies](./routing.md) — single-shot vs sequential, with optimal stopping.
- [Multi-agent support](./agents.md) — sticky sessions, agent fingerprinting, MCP/ACP translation.
