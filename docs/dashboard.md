---
title: Dashboard
description: CLI event viewer + web dashboard for spend & health
---

# Dashboard

Two ways to watch the router: a CLI event viewer for real-time routing decisions, and a web dashboard for spend, reliability, and calibration health.

## Web dashboard

Available at `http://localhost:8000/metrics/dashboard` while the router is running. Live-updating view of:

- **Total volume** — aggregate count of routed requests.
- **Accrued expense** — total USD spent across all providers.
- **Potential cost** — what you would have spent if every query had hit your flagship model. The counterfactual.
- **Total savings** — potential minus actual. Retained value.
- **Success density** — overall operational success rate.
- **Per-model metrics** — most reliable, most economical, fastest, least reliable, chattiest, most concise, clumsiest.
- **Per-agent activity** — requests, cost, tokens, and success rate for each detected client (Cursor, Zed, RooCode, …).
- **Probability calibration** — a live calibration curve showing how well Reality Check™'s predicted probabilities track observed outcomes.

> [!INFO]
> **Read the calibration plot.** The diagonal is perfect calibration: if the router predicts 70% success, 70% of those queries should succeed. The closer the live curve hugs the diagonal, the more honest the router's probabilities — which translates directly to better routing decisions.

## CLI event viewer

Open a second terminal alongside the running server and launch the viewer:

```bash
source venv/bin/activate
python reality-router/event_viewer.py
```

You'll see a live stream of routing decisions in real time.

### Controls

- `[Enter]` — manually refresh the list of recent events.
- `[1–5]` — inspect a recent event in full: features extracted, per-model utility breakdown, validation results.
- `[h]` — system health report. Color-coded reliability per model: 🟢 healthy, 🟡 unstable, 🔴 quarantined by circuit breaker.
- `[q]` — exit.

### Reset historical data

Clear all routing history, probability tables, and logs:

```bash
python reality-router/event_viewer.py --clear
```

> [!WARNING]
> **Heads up.** Clearing wipes Reality Check™'s learned probabilities for your install. The router starts fresh — useful for testing, but you'll lose accumulated calibration. Production installs should rarely need this.

## Where data lives

All user-specific data is stored under `~/.reality_router/`:

- `.env` — your provider API keys.
- `user_models.json` — model overrides (manual pricing, concurrency limits).
- `disabled_models.json` — models you've toggled off.
- `reality_router.db` — SQLite store for events, history, and calibration cache.
- `logs/` — text logs for debugging.

Nothing leaves this directory. If you need to migrate or back up an install, copy the folder.
