---
title: How it works
description: Expected Utility, probability updates, sentiment feedback
---

# How it works

RealityRouter scores every model on Expected Utility — a single number that combines accuracy, cost, and latency — and routes to the winner. The probabilities behind that math come from Reality Router™ calibration, updated continuously from real outcomes.

## Expected Utility

For every incoming request, the router computes the Expected Utility (EU) for each configured candidate model `m_i`:

```text
EU(m_i) = (p_i · R) - (α · c_scaled) - (β · t_i) - penalty_pref
```

### Precise Units and Definitions:

- **`p_i` (Probability of Success)**: The calibrated likelihood (`0.0` to `1.0`) that model `m_i` successfully satisfies the query and protocol constraints.
- **`R` (Baseline Reward)**: The value of a correct response, fixed at **`100.0`**.
- **`c_i` (Estimated Cost)**: Raw estimated dollar cost of running the request on model `m_i` in fractional USD (e.g. `$0.005`).
- **`c_scaled` (Scaled Cost)**: To prevent tiny fractional dollar costs from being dominated by multi-second latencies, **cost is scaled by `1000.0`** (milli-dollars). Thus, a cost of `$0.015` becomes `15.0` units in the utility equation.
- **`t_i` (Estimated Latency)**: Response latency in **seconds** (e.g. `2.5` seconds), calculated from a rolling user-specific sliding window.
- **`α` (Cost Sensitivity)**: Cost penalty weight coefficient (alpha). Modifiable dynamically via the dashboard slider.
- **`β` (Time Sensitivity)**: Latency penalty weight coefficient (beta). Modifiable dynamically via the dashboard slider.
- **`penalty_pref` (Preference Penalty)**: Represents model preference overrides. Defaults to `0.0` when model preference is `100.0`. Computed as `(100.0 - preference) * 10.0`.

### The Decision Rule

The router selects the model with the highest expected utility:

```text
m* = argmax [ (p_i · R) - (α · (c_i · 1000.0)) - (β · t_i) - penalty_pref ]
```

### Complete Numerical Example

Let's configure our weights as: **`α = 1.0`** and **`β = 2.0`**. We evaluate three candidate models for a standard coding task:

1. **Model A (Cheap Local Model)**:
   - Success Probability `p = 0.50`
   - Latency `t = 1.0s`
   - Cost `c = $0.000` (Local model, zero marginal cost)
   - *Utility calculation*: `EU = (0.50 * 100) - (1.0 * (0.000 * 1000)) - (2.0 * 1.0) = 50.0 - 0.0 - 2.0 = 48.0`

2. **Model B (Fast Flagship-Lite Model)**:
   - Success Probability `p = 0.85`
   - Latency `t = 1.5s`
   - Cost `c = $0.002` (Milli-dollars: `2.0` units)
   - *Utility calculation*: `EU = (0.85 * 100) - (1.0 * 2.0) - (2.0 * 1.5) = 85.0 - 2.0 - 3.0 = 80.0`

3. **Model C (Flagship Heavy Model)**:
   - Success Probability `p = 0.95`
   - Latency `t = 3.0s`
   - Cost `c = $0.030` (Milli-dollars: `30.0` units)
   - *Utility calculation*: `EU = (0.95 * 100) - (1.0 * 30.0) - (2.0 * 3.0) = 95.0 - 30.0 - 6.0 = 59.0`

**Result**: **Model B** has the highest Expected Utility (`80.0` vs. `48.0` vs. `59.0`) and is selected. Even though Model C has higher raw intelligence (95% success), its high cost penalty under `α = 1.0` lowers its utility. If cost sensitivity is set to `0.1`, Model C would win. If latency is critical, local Model A might win if Model B slows down. The math adjusts dynamically per call.

## Dynamic cost estimation

The cost term `c_i` isn't static — the router tracks per-token pricing for every model and adjusts in real time:

- **Automated Pricing Manager** pulls up-to-date input/output token prices from the LiteLLM open registry weekly. Your utility math always reflects what you actually pay.
- **Manual configuration** via `~/.reality_router/user_models.json` takes priority — useful for custom models, local instances, or negotiated enterprise pricing.
- **Context-aware** — the router tokenizes your query, combines that with each model's historical completion length, and penalizes cost accurately for large context windows (where pricing tiers often kick in).

## How probabilities get smarter

`p_i` is the hard part — and where Reality Router™ does the work. The router doesn't just store a fixed success rate per model. It tracks **per-model, per-task-type** probabilities that update continuously.

### 1. Unified feature extraction

Every request — regardless of strategy — is decomposed into a consistent set of features: AST complexity (for code), task type (refactor / explain / generate / review), trace frequencies, agent fingerprint (Cursor, Zed, Claude Code, etc.), prompt length, and more.

### 2. Reality Router™ calibration

These features are sent to the Reality Router calibration service, which compares the current request against historical outcomes for structurally similar requests. The result: `p_i` for each candidate model.

### 3. Sentiment feedback loop

The router watches the conversation for implicit feedback. If a user follows up with a correction, complaint, or "try again," a small sentiment model flags it as unhappy. The router lowers `p_i` for that model on that task type. Future similar prompts route elsewhere.

> [!NOTE]
> **Sentiment cost.** Sentiment analysis runs a background call to a cheap fast model you pick at setup (recommended: Claude Haiku or Gemini Flash). Adds a few cents per 1,000 requests.

### 4. Continuous learning

All signals — successful completions, quality failures, sentiment, validation errors — are logged and feed back into Reality Router™'s calibration. Tomorrow's routing reflects yesterday's outcomes. Without you ever filling out a survey.

## Why these probabilities can be trusted

The router is only as mathematically robust as the probabilities driving it. If `p_i` is miscalibrated, Expected Utility theory fails — leading to over-routing to weak models or unnecessary spending on flagship models. RealityRouter uses mathematical frameworks based on Venn-Abers calibration and conformal prediction to estimate model success probabilities.

### 1. The Target Outcome and Calibration Population
- **Target Outcome (Success)**: We define a request as "successful" (`Y = 1.0`) if the output is structurally and syntactically valid (Protocol Success) AND is accepted by the client without triggering dissatisfaction markers (Task/Sentiment Success). Any other outcome is a failure (`Y = 0.0`).
- **Calibration Population**: The calibration set consists of historic requests, classified by high-level extracted task features, that did not experience immediate infrastructure/network failures.

### 2. Standard Statistical Assumptions
- **Exchangeability**: The core calibration guarantee assumes that past and future requests of a given task type (e.g. "writing python tools") are exchangeable (i.e., their joint probability distribution is invariant under permutation).
- **Distribution Shift**: In reality, user behavior and prompt distributions shift over time (non-exchangeability). To adapt to distribution shift, RealityRouter applies a time-decaying recency weighting to calibration samples, prioritizing recent outcomes to dynamically track shifting model performance.
- **Cold-Start Handling**: For newly released models or cold-start task categories with no historical data, RealityRouter initializes `p_i` using conservative baseline capabilities from a global provider registry, quickly adapting as real local feedback events are logged.

### 3. Venn-Abers Calibration Guarantees
Venn-Abers predictors process task features and output a calibrated probability interval `[p_low, p_high]`.
- **Validity Guarantee**: Under the exchangeability assumption, Venn-Abers probabilities are guaranteed to be multipatially calibrated. That is, if the predictor assigns a probability of 70%, the true long-run observed success frequency is mathematically guaranteed to approach 70%, independent of the underlying distribution.
- **Mapping to expected utility `p_i`**: To calculate a concrete scalar Expected Utility score, the interval is mapped to a single probability estimate `p_i` using the game-theoretic minimax-regret selection:
  ```text
  p_i = p_high / (1.0 + p_high - p_low)
  ```

### 4. Conformal Prediction Coverage
For multi-class classifications or structured output boundaries, Conformal Prediction establishes a prediction set that contains the true required output with a provably bounded error rate:
- For a chosen significance level `ε` (e.g. 5%), the conformal set is guaranteed to cover the correct model performance category with a probability of at least `1 − ε` (95%), independent of distribution shapes.

### 5. Why Not LLM-as-Judge or Heuristic Calibrators?
- **LLM-as-Judge**: Introducing an evaluator LLM creates cascading errors, multiplying hallucinations and adding significant latency and API costs.
- **Learned Heuristic Calibrators**: Standard machine-learning classifiers (like neural networks or logistic regressions) are prone to overconfidence and lack any mathematical guarantees of calibration under distribution shift.

### 6. Dashboard Calibration Curve Calculation
The web dashboard calibration plot is computed by partitioning recent requests into probability bins (e.g. `[0.0, 0.2]`, `[0.2, 0.4]`, ...). For each bin, the average predicted probability is plotted against the actual observed fraction of success (`Y = 1.0`). If the curve hugs the diagonal, the router's utility estimations are statistically valid.

## Protocol & quality validation

Before any response is returned to your client, the router inspects the raw output for issues that would break an agent loop:

- **Unclosed Markdown** — broken code blocks (` ``` `)
- **Malformed JSON** — invalid tool calls or JSON data blocks
- **Broken agent tags** — unclosed `<thought>`, `<command>`, etc.
- **"Laziness"** — code that skips with `// ...existing code...`
- **AI refusals** — "As an AI language model…"
- **Heuristic truncation** — abrupt endings mid-word or on conjunctions

If anything trips, the router **silently escalates** to a better model. Negative feedback gets logged to Reality Router™. Your client sees only the clean response.

### Quality vs infrastructure failures

The router distinguishes between two failure modes:

- **Quality failures** (truncation, malformed syntax, refusals) → negative feedback to Reality Router + automatic escalation.
- **Infrastructure failures** (timeouts, API 500s, invalid keys) → **do not** contaminate Reality Router metrics. Instead, the router propagates HTTP 502 so you can fix what's actually broken.

## Next

- [Routing strategies](./routing.md) — single-shot vs sequential, with optimal stopping.
- [Multi-agent support](./agents.md) — sticky sessions, agent fingerprinting, MCP/ACP translation.
