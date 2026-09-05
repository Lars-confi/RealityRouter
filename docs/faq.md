---
title: FAQ
description: Cost, model choice, providers, privacy, and how this differs from other routers
---

# FAQ

## My AI coding bill keeps climbing. What can I actually do about it?

Most spend goes on trivial requests hitting flagship models. Formatting JSON and
renaming a variable cost the same as designing a migration, because the tool
sends everything to one model.

RealityRouter scores each request against every model you have configured and
picks the cheapest one likely to succeed. The expensive models stay available
for the work that needs them.

## I want to run more agents, but cost scales with every one I add.

An agent run is mostly routine steps — reading files, formatting, summarising,
checking its own work — with a handful of genuinely hard ones. Sent to a single
model, every step is priced like the hardest one, so cost scales linearly with
how much you run. That is what makes people ration their agents.

Routing per request breaks that link. The routine majority goes to cheap models
and only the hard minority reaches a flagship, which changes what running ten
times more agents actually costs.

This is the case where a router matters most: not trimming a bill you already
have, but making work viable that you would otherwise not run at all.

## I have API keys for several providers. How do I use them all from one tool?

Point the tool at RealityRouter instead of at a provider. It presents a normal
OpenAI-compatible endpoint, so nothing in your setup changes except a base URL,
and it routes across every provider you have configured.

See [Tool integrations](integrations.md).

## Which model should I use?

That is the question RealityRouter removes. It scores every model per request on
probability of success, cost and latency, and picks the best one — so the answer
changes per request instead of being a setting you guess at once and never
revisit.

## I keep hitting rate limits on my subscription.

A router spreads work across every provider you have keys for, so no single
account absorbs everything. It also cuts total volume to any one provider by
sending easy work to cheaper models.

## I run Ollama. Can I use my own hardware when it makes sense?

Yes. Local models sit in the same pool as cloud ones and compete on the same
terms. On fast hardware they win a lot of the work at zero marginal cost. On
slow hardware the latency term rules them out — which is the router doing its
job, not failing.

## How is this different from OpenRouter?

OpenRouter is a hosted service that resells model access. RealityRouter is
software you run yourself, with your own provider keys, and no traffic through
anyone else's infrastructure. It is MIT-licensed, and you can read exactly how
routing decisions are made.

## How is this different from LiteLLM?

LiteLLM is a proxy: it standardises calling many providers, and you choose the
model. RealityRouter chooses for you, per request, on expected utility. See
[How it works](concepts.md).

## Will this make my agent worse?

It routes easy work to cheaper models and keeps flagships for the rest. The
dashboard shows per-model success rates so you can see whether that trade held
for your workload.

If a model starts failing, a circuit breaker takes it out of rotation until it
recovers.

## How much will I save?

It depends entirely on your workload and which models you configure. Anyone
quoting a fixed percentage is guessing.

The dashboard shows what you actually spent against what the same requests would
have cost on your most expensive configured model, so you measure it rather than
trust it.

## Can I cap my spend?

Not today. RealityRouter reduces cost by routing, and shows you where the money
went, but it does not enforce a budget ceiling.

## Do my prompts go through your servers?

No. You run it, it holds your keys, and it calls providers directly from your
machine.

## How long does setup take?

A few minutes. There is a one-line installer and a setup wizard — see the
[Quickstart](quickstart.md) — and a coding agent can do the whole thing if you
ask it to, including wiring up your editor. See
[Agent-assisted install](agent-install.md).
