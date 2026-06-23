# RealityRouter

**The Intelligent Decision Engine for AI Agents.**

RealityRouter is a high-performance LLM routing gateway designed for the
agentic era. Standard proxies pass requests through; RealityRouter uses
**Expected Utility Theory** and real-time calibration to choose the best model
for every individual prompt — balancing accuracy, cost, and latency.

In a world where model performance fluctuates and costs vary by orders of
magnitude, RealityRouter puts the choice back in the hands of the user.

---

## Why RealityRouter?

For developers building AI agents (Zed, Cursor, Claude Code, Roo Code,
OpenClaw, AutoGPT), picking a model is usually a trade-off between
"too expensive" (flagship models on every query) and "too unreliable"
(small local models that miss). RealityRouter solves this by acting as
smart middleware that:

1. **Evaluates intelligence** — uses the Reality Signal™ API to estimate the
   probability of success for each model on the specific task at hand.
2. **Calculates utility** — applies a mathematical formula to balance
   accuracy, cost, and speed.
3. **Enforces quality** — validates tool calls before they reach your agent,
   catching malformed JSON, ghost tool calls, and protocol leaks.

---

## The core engine — Expected Utility

Every request is passed through a decision-theoretic engine. For each
candidate model `m` in your configured pool, RealityRouter computes:

```
EU(m) = p · R − α · cost − β · latency
```

- `p` — calibrated probability of success on this specific prompt.
- `R` — constant reward for a correct answer.
- `α` — your cost sensitivity (tune in the dashboard).
- `β` — your latency sensitivity (tune in the dashboard).

The router picks `argmax EU` — every model, every query.

> **Dynamic tuning.** The dashboard exposes `α` and `β` as live sliders.
> Slide left for maximum frugality; slide right for raw speed. Changes take
> effect on the next request — no restart, no redeploy.

---

## Validation gateway

RealityRouter sits between the model and your agent to enforce protocol
compliance:

- **Leak protection** — detects and scrubs raw tool tags that models
  accidentally leak into text output.
- **Ghost tool detection** — rejects responses that call tools you didn't
  expose to the model.
- **Heuristic rescue** — recovers valid JSON tool calls buried in
  conversational fluff.
- **Schema validation** — validates tool arguments against your schema
  before the agent ever sees them.

---

## Key features

- **Two routing strategies** — single-shot (route to argmax-EU model
  immediately) or sequential (start cheap, escalate on validation failure).
- **Automatic feedback loop** — validated outcomes feed back into the
  calibration engine, sharpening future routing decisions.
- **Multi-provider auto-discovery** — bring your own keys; the router
  discovers and benchmarks models from OpenAI, Anthropic, Gemini, Mistral,
  DeepSeek, local Ollama, and any OpenAI-compatible endpoint.
- **Live dashboard** — track unit economics, savings vs. always-flagship,
  per-model reliability, and per-agent activity in a built-in web UI.

---

## Documentation

- **[Quickstart](docs/quickstart.md)** — install and route your first
  request in about 60 seconds.
- **[Architecture](ARCHITECTURE.md)** — directory layout and component
  overview.
- **[How it works](docs/concepts.md)** — Expected Utility math and the
  calibration feedback loop.
- **[Routing strategies](docs/routing.md)** — single-shot vs. sequential.
- **[Multi-agent support](docs/agents.md)** — protocol detection and
  sticky sessions.
- **[API reference](docs/api.md)** — OpenAI-compatible endpoints.
- **[Dashboard](docs/dashboard.md)** — CLI event viewer + web dashboard.

---

## Developer integration

RealityRouter is **100% OpenAI API compatible**. You don't need to rewrite
your agent — just change your environment variables:

- **Base URL** — `http://localhost:8000/v1`
- **API Key** — `any` (or your configured secret)
- **Model** — `auto` (or any model name; the router intercepts and chooses
  the best actual model for the job)

---

## Contributing

We're building user-centric AI infrastructure. If you're interested in
decision theory, agent protocols, or high-performance routing, contributions
are welcome.

Built by Confidentia AI and the open-source community.