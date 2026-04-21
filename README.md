# LLMRerouter by Confidentia AI and friends powered by Reality Check

A smart rerouter designed to maximize utility for LLM usage across various tools and agents. This system intelligently routes requests to different language models based on **Expected Utility Theory (EUT)**, optimizing for cost, time, and accuracy without requiring users to manually select models for every task.

## Overview

The LLMRerouter acts as a transparent proxy between your AI clients (agents, IDEs, scripts) and multiple LLM providers. By analyzing the complexity of each request and tracking historical performance, it ensures that high-stakes tasks get the best models while simple tasks use faster, cheaper alternatives.

## API Endpoints

The system exposes standard OpenAI-compatible endpoints, making it a drop-in replacement for most LLM integrations:

- `POST /v1/chat/completions` - For chat-based interactions
- `POST /v1/completions` - For legacy text completion requests

## How It Works

The routing happens transparently using a mathematical framework to balance trade-offs.

### Expected Utility Formula

For each available model $m_i$, the system calculates:

$$EU(m_i) = p_i \cdot R - \alpha \cdot c_i - \beta \cdot t_i$$

- **$p_i$ (Probability)**: The estimated likelihood of the model providing a correct or high-quality answer.
- **$R$ (Reward)**: The value assigned to a successful outcome.
- **$c_i$ (Cost)**: The estimated cost based on the prompt size, historical completion length, and the model's specific input/output token pricing.
- **$t_i$ (Time)**: The estimated latency for the model response.
- **$\alpha, \beta$ (Sensitivities)**: User-configurable weights for cost and time penalties.

### Automatic Probability Updates

The core intelligence of the LLMRerouter lies in how it updates $p_i$ (the probability of success) automatically:

1. **Unified Feature Extraction**: Every request—regardless of strategy—is analyzed for a consistent set of structural and semantic features (e.g., AST complexity, trace frequencies, and agent fingerprints).
2. **Reality Check Calibration**: These features are sent to a calibration service that compares the current request against historical outcomes for similar tasks.
3. **Sentiment Feedback Loop**: The router monitors the conversation for implicit feedback. If a user follows up with a correction or complaint, the system detects "unhappy" sentiment and lowers the success probability for that model/task pair.
4. **Continuous Learning**: These signals are logged and used to update the weights of the selection algorithm in real-time.

### Dynamic Cost Estimation & Pricing

To accurately calculate Expected Utility and track your actual spend in the Web Dashboard, the system differentiates between input (prompt) and output (completion) token costs:

- **Automated Pricing Manager**: The router features an automated Pricing Manager that fetches up-to-date token costs from the open-source community's standard registry (maintained by LiteLLM). It downloads the latest pricing weekly and caches it locally, ensuring your utility calculations always use accurate real-world data without manual intervention.
- **Manual Configuration**: You can still explicitly set `prompt_cost` and `completion_cost` (price per 1k tokens) for custom or local models in your `user_models.json`. These manual settings will take priority over the automated web registry.
- **Context-Aware Utility**: Before making a routing decision, the system estimates the input token count of your query and combines it with the model's historical output lengths to accurately estimate and penalize the cost ($c_i$) of large context windows based on its specific pricing tier.

## Agent Protocol Validation

To ensure autonomous agents (like OpenClaw, AutoGPT, etc.) don't crash from broken responses, the LLMRerouter includes an active **Formatting & Syntax Validator**.

Before returning an answer to your client, the router inspects the raw output for:

- **Unclosed Markdown**: Broken code blocks (` ``` `).
- **Malformed JSON**: Invalid tool calls or JSON data blocks.
- **Broken Agent Tags**: Unclosed XML elements (e.g., `<thought>`, `<command>`).
- **"Laziness"**: Skipping code with `// ... existing code ...`.
- **AI Refusals**: Responses like *"As an AI language model..."*.
- **Heuristic Truncation**: Abrupt endings in the middle of words or conjunctions (e.g., ending with "the", "and").

If any of these are detected, the router treats the response as an objective failure, instantly sends negative feedback to Reality Check, and **automatically escalates** to a better model without the agent ever seeing the broken text.

### Infrastructure vs. Quality Failures

The router distinguishes between model "quality" and system "infrastructure":

- **Quality Failures**: (Truncation, malformed syntax) Trigger negative feedback and automatic escalation.
- **Infrastructure Failures**: (Connection timeouts, API 500s, invalid keys) These do **not** trigger Reality Check feedback. Instead, the router immediately propagates an `HTTP 502 Bad Gateway` to the calling agent so the user can address the connectivity or configuration issue.

## Multi-Agent Protocol Support

The LLMRerouter features a sophisticated **Protocol Identification & Transformation Layer** that goes beyond simple headers to natively support complex agentic workflows across different environments.

### Protocol Identification Layer
The router natively detects and adapts to specific clients like **Zed**, **VSCodium/Continue**, and **OpenClaw**. This allows the system to apply client-specific routing rules, format responses correctly, and manage state in a way that matches the expectations of the calling agent.

### Dynamic Agent Discovery
For agent-to-agent (A2A) communication, the router exposes a standard `/.well-known/agent-card.json` endpoint. This allows clients like OpenClaw to dynamically discover the rerouter and its supported capabilities, which include advanced tool sets like `codebase-edit`, `filesystem-search`, and `mcp-proxy`.

### Multi-Agent Sticky State
To prevent "split-brain" routing (where consecutive messages in a single tool-call loop are sent to different models), the system maintains a **Multi-Agent Sticky State**. Once a conversation begins, the router locks that session to a specific model. This is achieved using:
- **Explicit Session IDs**: Native support for session tracking provided by clients like OpenClaw and Continue.
- **Synthesized IDs**: For clients like Zed that don't provide explicit session tracking, the router synthesizes and maintains state seamlessly.

### MCP/ACP Translation Layer
Different models have varying levels of support for function calling and specific schema requirements. The router includes a translation layer that handles this automatically:
- **Seamless Interception**: It intercepts tool calls from clients like Zed (using the Agent Client Protocol) and standardizes them into the format required by the target model.
- **Graceful Fallbacks**: If the optimal model chosen for a task does not natively support `function_calling`, the router gracefully strips the tools from the request and injects a fallback prompt, allowing the model to answer the query without breaking the agent's workflow.

## Routing Strategies

The LLMRerouter supports two distinct strategies for model selection, selectable during setup:

### 1. LLM Routing (Single-shot)

The default mode. The system evaluates all configured models simultaneously and routes the query to the single best candidate.

- **Best for**: Balanced performance and low latency.
- **Logic**: Immediately selects the model $m_i$ that maximizes $p_i \cdot R - \alpha \cdot c_i - \beta \cdot t_i$.

### 2. LLM Rerouting (Sequential)

A multi-stage approach where the system attempts to solve the query with cheaper models first, verifying the output quality before deciding whether to stop or escalate.

- **Best for**: Maximum cost optimization and autonomous agentic workflows.
- **Process**:
    1. Starts with the cheapest available model.
    2. **Post-response Calibration**: After a response is received, the system re-runs feature extraction, appending post-hoc diagnostic metrics (confidence scores, entropy, and logprobs).
    3. **Validation**: A calibrated probability $p_{actual}$ is calculated using the full feature set.
    4. **Optimal Stopping**: Compares the utility of the current answer against the potential utility of escalating to a more powerful model.
    5. Escalates only if the potential gain in accuracy (assuming $p_{next}=1.0$ for the "Gold" model) outweighs the additional cost and time penalties.

## Installation & Setup

The LLMRerouter features an interactive setup wizard to configure your environment in minutes.

1. **Initialize**: Run the startup script `./start.sh`.
2. **Select Strategy**: Choose between **LLM Routing** (Single-shot) or **LLM Rerouting** (Sequential).
3. **Tune Coefficients**: Set your sensitivities for Reward ($R$), Cost ($\alpha$), and Time ($\beta$).
4. **Connect Providers**: Enter API keys for your preferred LLM providers (OpenAI, Anthropic, Gemini, etc.).
5. **Manage Models**: Toggle discovered models **ON** or **OFF**, set **Concurrency Limits**, and select your preferred model for **Sentiment Analysis**.

### Concurrency Limits (Thread Limits)

To prevent overwhelming specific providers or local instances (like Ollama), you can set a `thread_limit` or `concurrency_limit` per model in your `user_models.json`.

- **Behavior**: The router maintains an internal semaphore for each model.
- **Auto-Bypass**: If a model has reached its maximum number of concurrent requests, the router will **automatically skip** it and route to the next best available model in the ranked list. This ensures high availability even when specific models are busy.

> **Note on Sentiment Cost**: The system must make a background API call to your chosen Sentiment model to assess if the user was happy or unhappy with an answer. This will incur a small token cost. It is recommended to select a cheap, fast model (like Claude 3 Haiku or Gemini 1.5 Flash) for this specific task.

## Usage

### 1. Start the Server

Run the router from the project root:

```bash
./start.sh
```

The server starts by default on `http://localhost:8000`.

### 2. Configure Your Client

Point your AI agent, IDE extension, or custom script to the router. Since the API is OpenAI-compatible, you usually only need to change the `Base URL`:

- **Base URL**: `http://localhost:8000/v1`
- **API Key**: (Any value; the router uses its own internal `.env` keys to talk to providers)

### Automatic Agent Identification

The router automatically identifies which tool or agent is calling it to improve performance tracking and Reality Check calibration. It checks for identification in this order:

1. **Request Body**: An optional `agent_id` field in the JSON payload.
2. **Custom Header**: An `X-Agent-ID` HTTP header.
3. **Standard Header**: The standard `User-Agent` header (automatically sent by tools like **Zed**, **VS Code**, and **Cursor**).

If no identification is found, it defaults to `default`. You can monitor these IDs and their specific success rates in the [Dashboard](#dashboard--debugging).

## Dashboard & Debugging

The LLMRerouter includes a built-in CLI dashboard for real-time monitoring of routing decisions.

### Launching the Dashboard

To see the utility calculations and routing decisions in real-time, open a **separate terminal** and run the CLI viewer:

```bash
source venv/bin/activate
python llm-router/event_viewer.py
```

### Web Dashboard

For a high-level overview of system health and model economics, you can access the Web Dashboard while the server is running:

- **URL**: [http://localhost:8000/metrics/dashboard](http://localhost:8000/metrics/dashboard)

This dashboard provides a live-updating view of:

- **Total Request Volume**: Aggregate count of all routed queries.
- **Accrued Expense**: Total cost across all providers in USD.
- **Model Reliability**: Success rates and latency averages for every configured model.

### CLI Dashboard Usage

While the dashboard is active, you can interact with it using:

- **[Enter]**: Manually refresh the list of recent events.
- **[1-5]**: Enter a number to inspect the full request/response payload and utility breakdown for that event.
- **[q]**: Exit the viewer.

To reset all historical performance data and logs, you can run:

```bash
python llm-router/event_viewer.py --clear
```

### Features

- **Real-time Event Stream**: Watch agents interact with different models live.
- **Detailed Inspection**: View the exact features extracted from a prompt and how they influenced the final probability score.
- **Utility Comparisons**: See the "leaderboard" of models for every single request.
- **Sentiment Tracking**: Monitor how the system is interpreting user feedback to tune its future decisions.