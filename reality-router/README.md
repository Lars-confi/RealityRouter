# Reality Router: Core Architecture & Contribution Guide

An intelligent, modular routing engine that applies **Expected Utility Theory (EUT)** to language model selection. This system acts as a high-performance middleware layer designed to optimize agentic workflows for cost, latency, and response quality.

## System Architecture

The Reality Router is built on a modular Python/FastAPI backend that decouples routing logic from model execution. It utilizes a stateful feedback loop to calibrate routing decisions based on real-time performance and user sentiment.

### Core Components

#### 1. Router Engine (`src.router.core`)
The central orchestrator implementing multi-strategy routing logic.
- **Unified Feature Extractor**: Analyzes incoming queries for 40+ structural and semantic features (AST complexity, trace frequencies, etc.).
- **Utility Calculator**: Implements the mathematical framework: $EU(m_i) = p_i \cdot R - \alpha \cdot c_i - \beta \cdot t_i$.
- **Validation Layer**: Intercepts model responses to verify schema compliance (JSON, XML, Markdown) before delivery.

#### 2. Reality Check Integration
The system integrates with Reality Check calibration endpoints to obtain success probabilities ($p_i$).
- **LLM Routing Endpoint**: Used for single-shot calibration during initial ranking.
- **LLM Rerouting Endpoint**: Used for high-fidelity post-hoc assessment in tiered strategies.
- **Sentiment Feedback Loop**: Asynchronous submission of user feedback to reinforce or penalize model performance.

#### 3. Adapter Layer (`src.adapters`)
A provider-agnostic interface that normalizes requests across different backends.
- **Normalized Interfaces**: Support for OpenAI, Anthropic, Gemini, and Generic (Ollama/vLLM) providers.
- **Concurrency Controller**: Uses per-adapter semaphores to manage `thread_limit` constraints.

#### 4. Persistence Layer (`src.models.database`)
A SQLAlchemy-managed store that enables continuous learning.
- **Routing Logs**: Stores full payloads, extracted features, and utility metrics.
- **Performance Tracking**: Maintains rolling averages for model latency and success rates.

---

## Technical Stack
- **Backend**: FastAPI (Asynchronous I/O)
- **Database**: SQLAlchemy (SQLite/PostgreSQL)
- **Networking**: HTTPX (Async client)
- **Validation**: Pydantic v2

---

## Contributing to Reality Router

We welcome contributions from the community. Follow these guidelines to help improve the routing engine.

### How to Contribute
1. **Feature Implementation**: Add support for new model providers or improve feature extraction logic.
2. **Bug Fixes**: Resolve issues in the validation layer or improve error handling for specific adapters.
3. **Documentation**: Enhance technical guides or clarify architectural components.
4. **Testing**: Expand the `pytest` suite to cover edge cases in sequential rerouting.

### Development Workflow
1. **Fork & Clone**: Create a local copy of the repository.
2. **Environment Setup**:
   ```bash
   pip install -r requirements.txt
   python setup.py develop
   ```
3. **Branching**: Create a descriptive branch (e.g., `feature/new-adapter` or `fix/validation-regex`).
4. **Testing**: Ensure all tests pass before submitting.
   ```bash
   pytest tests/
   ```
5. **Pull Requests**: Submit your PR with a detailed description of the changes and any architectural impact.

### Code Standards
- Follow PEP 8 guidelines.
- Use type hints for all new function signatures.
- Ensure all new features are accompanied by relevant unit or integration tests.

---

*Reality Router is maintained by Confidentia AI and friends, powered by Reality Check.*