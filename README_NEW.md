# 🚀 Reality Router

### **The Intelligent Backbone for High-Performance AI Agents**
*Powered by Expected Utility Theory and Reality Check™ Calibration*

**Reality Router** is not just another LLM proxy. It is a sophisticated, mathematically-driven routing engine designed to bridge the gap between raw LLM inference and the mission-critical reliability required by modern AI Agents (like Roo Code, OpenClaw, and more).

Whether you are trying to cut costs without sacrificing intelligence, or need a "Ladder" escalation strategy to ensure code correctness, Reality Router dynamically optimizes your requests in real-time.

---

## 🧠 The Core Engine: Expected Utility Theory (EUT)

At the heart of Reality Router is a decision-theoretic engine that evaluates every model in your pool against a rigorous utility formula:

$$EU(m) = p \cdot R - \alpha \cdot cost - \beta \cdot time$$

- **$p$ (Probability):** The calibrated likelihood that model $m$ will succeed on your *specific* prompt, provided by the **Reality Check API**.
- **$R$ (Reward):** The value of a successful response.
- **$\alpha$ (Cost Sensitivity):** How much you care about saving money.
- **$\beta$ (Time Sensitivity):** How much you care about low latency.

### **✨ NEW: Dynamic Preference Tuning**
The Reality Router Dashboard now features an interactive **Preference & Cost vs. Time Slider**. Change your routing logic on the fly—move to the left for maximum frugality, or slide to the right when speed is everything. The system injects these coefficients into the running core immediately.

---

## 🛡️ Strict Validation Gateway (The "Guardian")

LLMs often hallucinate tool syntax, leak internal tags, or produce malformed JSON. Reality Router acts as a **Strict Protocol Enforcement Layer**:

- **Content Leak Protection**: Detects and escalates when models leak raw tool tags (`<function>`, `✿`, etc.) into text blocks.
- **Ghost Tool Detection**: Immediately rejects responses that call tools not present in the original request.
- **JSON Argument Validation**: Ensures every tool call is perfectly parseable before it ever reaches your agent.
- **Heuristic Tool Rescue**: Automatically "rescues" valid JSON tool calls that models accidentally bury in text blocks.

---

## 🚀 Key Features

### **1. Multi-Agent Protocol Support**
Reality Router is designed for a world of many agents.
- **Agent Identification**: Automatically resolves agent IDs from headers or payloads (Roo Code, Zed, etc.).
- **Multi-Agent Sticky State**: Ensures a consistent model experience for specific "sessions" within an agent.
- **Agent Cards**: Exposes aggregate capabilities via `/.well-known/agent-card.json` for dynamic discovery.

### **2. Advanced Routing Strategies**
- **Snap (Single-shot)**: The fastest route. Picks the highest utility model and fires.
- **Ladder (Sequential)**: The "Gold Standard" for reliability. Attempts the cheapest capable model, validates the output with Reality Check, and automatically escalates up the "intelligence ladder" if the response is low-confidence or malformed.

### **3. Automatic Feedback Loop**
The system learns in real-time.
- **Automated "Happy" Feedback**: Every time a model successfully executes a validated tool call, the router automatically sends positive reinforcement to the Reality Check API.
- **Sentiment Analysis**: Integrates a dedicated sentiment model (like `nemotron-3-nano`) to analyze user follow-ups and grade model performance.

### **4. Bulletproof Infrastructure**
- **Circuit Breakers**: Automatically trips and disables models that hit 400-errors or consistent failures (e.g., Google API rate limits).
- **Auto-Discovery**: Point Reality Router at your local Ollama instance or provide API keys for Gemini, Mistral, OpenAI, and more; it will automatically discover, benchmark, and price every available model.
- **Native Windows Support**: Full `install.ps1` and `start.ps1` scripts for a seamless PowerShell experience.

---

## 📊 Live Dashboard & Event Trace

Monitor your intelligence swarm with the built-in **Control Center** (`/metrics/dashboard`):

- **Unit Economics**: Track total cost, savings vs. "dumb" routing, and throughput.
- **Model Performance**: View box-plots for Latency, Utility, and Probability distribution across your pool.
- **Recent Events Trace**: A real-time, side-by-side view of Agent Activity and a detailed "Event Log" showing exactly how the router ranked models for the last 5 calls.

---

## ⚡ Quick Start

### **Linux / macOS / Git Bash**
```bash
curl -fsSL https://raw.githubusercontent.com/Lars-confi/RealityRouterTemp/main/install.sh | bash
```

### **Windows (PowerShell)**
```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force; iex ((New-Object System.Net.WebClient).DownloadString('https://raw.githubusercontent.com/Lars-confi/RealityRouterTemp/main/install.ps1'))
```

---

## 🛠️ Developer Integration

Reality Router is 100% OpenAI API compatible. Just point your client to:

- **Base URL:** `http://localhost:8000/v1`
- **API Key:** `any` (or your configured custom key)
- **Model:** `gpt-4o` (The router will intercept this and route to the best available model in your pool).

---

*Built with ❤️ by Confidentia AI and friends.*