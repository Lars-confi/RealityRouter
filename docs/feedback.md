# Feedback, Success Metrics & Calibration Reference (`feedback.md`)

RealityRouter uses closed-loop feedback systems to continuously calibrate model utility predictions. This document details the exact definitions, triggers, and calibration labels used by local metrics databases and remote Reality Signal layers.

---

## 1. Classifying Feedback & Success Signals

RealityRouter categorizes success along four distinct dimensions to prevent mixing up network issues with model capabilities.

### Protocol Success
- **Definition**: Was the model output structurally valid and parseable according to client expectations (such as valid JSON schemas, correct tool/function schemas, or expected XML tags)?
- **Trigger**: Local parsing parser validations succeed or fail.
- **Scope**: Local & Remote.
- **Immediate Impact**: A protocol failure triggers immediate fallback escalation (especially under the Ladder/sequential assessment flow).

### Task Success
- **Definition**: Did the model actually solve the user's intent?
- **Trigger**: Human-provided signal (e.g. thumb up/down in IDE, or accepting a suggested diff) or automated agent task validation (e.g. tests passing).
- **Scope**: Local & Remote.

### User Feedback
- **Definition**: Was dissatisfaction inferred from the conversation flow (such as the user telling the agent "no, that is wrong" or "re-do that step")?
- **Trigger**: The local Sentiment Feedback Loop analyses follow-up requests using the cheap sentiment evaluation model (`SENTIMENT_MODEL_ID`).
- **Scope**: Local. Inferred dissatisfaction decreases the probability weighting of the model that generated the preceding output.

### Infrastructure Success
- **Definition**: Did the API call complete normally without network, rate-limiting (HTTP 429), server (HTTP 5xx), or authentication (HTTP 401) errors?
- **Trigger**: Checked on every request transport lifecycle.
- **Scope**: Local.
- **Special Rule**: Infrastructure failures are **strictly excluded** from model performance calibration (the model is not penalized for temporary network or credential issues). It does, however, trigger the circuit breaker for that provider pool.

---

## 2. Calibration Labels & Signals Matrix

| Signal Name | Label Value | Triggering Source | Remote? | Immediate Adjust? | Affects History? | Excludes Infra Failures? | Reset/Delete Support? |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **`validation_success`** | `1.0` (True) | Local parser successfully parsed JSON/tool call | Yes | Yes | Yes | Yes | Yes (via database wipe) |
| **`validation_failure`** | `0.0` (False) | Local parser failed to decode output | Yes | Yes | Yes | Yes | Yes |
| **`sentiment_satisfied`**| `1.0` | Sentiment model determines user follow-up is neutral/positive | No | No | Yes | Yes | Yes |
| **`sentiment_rejected`** | `0.0` | Sentiment model detects user dissatisfaction / rejection | No | Yes | Yes | Yes | Yes |
| **`network_exception`**  | `N/A` | Provider API throws 5xx or times out | No | No (Bypasses Calibration) | No | Yes (Infra code handles separately) | No |

---

## 3. How Signals Drive Venn-Abers & Conformal Calibration

To maintain mathematically valid utility predictions:
1. **Calibration Population**: Only requests that complete with a valid model completion (i.e. `network_exception` is false) are sent to the Reality Signal calibration pool.
2. **Exchangeability Assumption**: We assume user queries within similar agent sessions share similar task classifications. 
3. **Probability Adjustments**: A sequence of `validation_failure` or `sentiment_rejected` labels immediately updates local weights. Over time, the computed utility `p_i` (the probability of task success) for that model decreases, automatically routing future similar tasks to more robust (and possibly more expensive) models.
4. **Wiping/Resetting History**: You can reset your local database and delete regional historical profiles using:
   ```bash
   reality-router setup --agent --set-clean
   ```
   or by manually removing `~/.reality_router/router.db`.
