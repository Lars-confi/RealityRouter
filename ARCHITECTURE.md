# Project Architecture

## Overview
This project is designed as a modular Reality routing system that allows for intelligent routing of requests to different language models based on various criteria such as model capabilities, cost, performance, and availability.

## Directory Structure
```
.
├── ARCHITECTURE.md          # This file
├── README.md                # Project overview and documentation
├── reality-router/          # Main project source code
│   ├── src/                 # Source code
│   ├── tests/               # Test files
│   ├── config/              # Configuration files
│   ├── docs/                # Documentation
│   ├── requirements.txt     # Python dependencies
│   └── setup.py             # Project setup
└── .gitignore               # Git ignore rules
```

## Core Components

### 1. Router Core
The main routing logic that evaluates requests and determines the appropriate model to route to.

### 2. Model Adapters
Components that handle communication with different LLM providers (OpenAI, Anthropic, etc.).

### 3. Load Balancer
Distributes requests across available models to optimize performance and cost.

### 4. Metrics Collector
Tracks performance, usage, and cost metrics for all routing decisions.

### 5. Configuration Manager
Handles dynamic configuration of routing rules and model settings.

## Technology Stack
- Language: Python
- Framework: FastAPI
- Testing: pytest
- Documentation: Markdown

## Python Dependencies
The project requires the following Python dependencies (listed in requirements.txt):
- fastapi==0.104.1
- uvicorn==0.24.0
- pydantic==2.5.0
- python-dotenv==1.0.0
- openai==1.3.5
- anthropic==0.7.0
- mistralai>=1.0.0
- requests==2.31.0
- numpy==1.24.3
- pandas==2.0.3
- pytest==7.4.0
- pytest-asyncio==0.21.0

## Development Workflow
1. Code changes in the `reality-router` directory
2. Run tests to ensure functionality
3. Update documentation in the `docs` directory
4. Commit changes to Git

## API Endpoints

The system exposes the following REST API endpoints:

- `POST /v1/completions` - For text completion requests
- `POST /v1/chat/completions` - For chat-based requests
- `GET /health` - Health check endpoint
- `GET /metrics` - Metrics and statistics endpoint

All endpoints follow the standard OpenAI API format for compatibility.

## Python Component Structure

### 1. Router Core
The main routing logic implemented as a FastAPI application with:
- Request parsing and validation
- Routing decision algorithms
- Model selection criteria
- Error handling and logging

### 2. Model Adapters
Python classes for each LLM provider:
- OpenAI adapter
- Anthropic adapter
- Mistral adapter
- Generic adapter framework

### 3. Load Balancer
Distributed load balancing with:
- Round-robin distribution
- Performance-based routing
- Rate limiting support
- Health checks

### 4. Metrics Collector
Real-time metrics with:
- Request/response timing
- Cost tracking
- Model utilization
- Performance analytics

### 5. Configuration Manager
Dynamic configuration handling:
- Environment-based settings
- YAML/JSON configuration files
- Hot-reloading capabilities
- Validation and defaults

## Deployment
The system is designed to be deployed as a microservice that can be scaled horizontally.

## Architecture Summary

This Reality Router project is designed as a modular, scalable system for intelligent routing of requests to different language models. The architecture follows Python best practices with:

1. **Modular Design**: Each component (router, adapters, load balancer, etc.) is separated into distinct modules
2. **Extensible Framework**: Easy to add new LLM providers through adapter pattern
3. **Configuration-Driven**: Routing rules and settings are managed through configuration files
4. **Monitoring Ready**: Built-in metrics collection for performance tracking
5. **Testable**: Each component is designed to be independently testable

The system is built with FastAPI for the web framework, providing automatic API documentation and validation, and is designed to be easily deployable in containerized environments.

## Data Flow and Routing Process

1. **Request Reception**: Incoming requests are received via API endpoints
2. **Request Analysis**: Query is analyzed for complexity and requirements
3. **Model Evaluation**: All configured models are evaluated using Expected Utility Theory
4. **Decision Making**: Optimal model is selected based on calculated expected utility
5. **Request Forwarding**: Request is forwarded to selected model
6. **Response Handling**: Response is processed and returned to user
7. **Metrics Collection**: All decisions and outcomes are logged for analytics

This process ensures optimal routing decisions that maximize the value of correct answers while minimizing cost and time.

## Automatic Routing Process

The system uses the following criteria to automatically select the best LLM based on Expected Utility Theory framework:

### Expected Utility Calculation

The system evaluates all configured LLMs based on:
- Cost (ci): Token usage for input and output
- Time (ti): Latency of the model response
- Probability (pi): Historical success rate for similar requests

This approach ensures optimal routing decisions based on the Expected Utility Theory framework, maximizing the value of correct answers while minimizing cost and time.

### Theoretical Foundation: Expected Utility Theory

#### Defining the System Components

First, let's define the variables in our decision environment:

- The Action Space (M): This is the set of available LLMs you can route to.
- The Query (q): The incoming question that needs to be routed.
- The Parameters (per model mi for a given query q):
  - ci: The estimated token cost to query model mi.
  - ti: The estimated latency (time) it takes for model mi to answer.
  - pi: The probability that model mi provides a correct or satisfactory answer (0 ≤ pi ≤ 1).

#### Constructing the Utility Function

A utility function assigns a numerical value to a specific outcome, representing how "good" or "desirable" that outcome is.

Our system incurs the cost (ci) and the time delay (ti) regardless of whether the LLM is correct or not. The only variable outcome is correctness. Let's introduce weighting factors to balance these different units:

- R: The inherent reward or value of getting a correct answer.
- α: Your sensitivity to cost (how much utility you lose per cent spent).
- β: Your sensitivity to time (how much utility you lose per second of delay).

We can define the utility U of an outcome for model mi in two scenarios:

**Scenario A: The model is correct**
Ui(correct) = R - αci - βti

**Scenario B: The model is incorrect**
Ui(incorrect) = 0 - αci - βti

#### Formulating the Expected Utility

Because you don't know in advance if the model will be correct, you must calculate the Expected Utility (EU) for each model. Expected utility is the sum of the utilities of all possible outcomes, weighted by their probabilities.

For a given model mi:

EU(mi) = pi ⋅ Ui(correct) + (1 - pi) ⋅ Ui(incorrect)

Substituting our utility functions into the equation:

EU(mi) = pi(R - αci - βti) + (1 - pi)(-αci - βti)

If we expand and simplify this algebraic expression, a beautiful and intuitive formula emerges:

EU(mi) = pi ⋅ R - αci - βti

Translation: The expected utility of an LLM is the value of a correct answer scaled by its probability of being right, minus the deterministic cost, minus the deterministic time penalty.

#### The Decision Rule (Optimization Problem)

Your router's job is to select the model that maximizes this expected utility. We write this formally using the argmax operator, which means "the argument (model) that outputs the maximum value."

m* = argmax_{mi∈M} [pi ⋅ R - αci - βti]

#### Simplified Parameter Approach

To make the system more user-friendly, we can simplify the parameter configuration:

**Parameter Configuration**
Users must configure the following parameters during setup:
- R (Reward): The inherent reward or value of getting a correct answer.
- α (Cost Sensitivity): Sensitivity to cost (how much utility you lose per cent spent).
- β (Time Sensitivity): Sensitivity to time (how much utility you lose per second of delay). If not set fallback to the relationship β = 1 - α, which reduces the number of parameters users need to configure.

**Cost Calculation**
- ci (Cost): Can be set by the user as cost per million tokens for both input and output or to a default value in $ per 1M tokens, one value for input and one for output, ci is actually the sum of these two. Perhaps we need to relate this to actual values for the user and the tool the user is using Zed, openclaw, etc.
- ti (Time): Can be assumed to be 1 second initially, then updated based on actual call latencies for that user

#### Practical Implementation Considerations

To actually build this, you will need to estimate pi, ci, and ti dynamically:

**Estimating Cost (ci)**
This is highly predictable. You can count the input tokens of q using a tokenizer and multiply by the provider's input token price. You can estimate output cost by keeping a running average of output lengths for similar queries. Users can set cost per million tokens during setup.

**Estimating Time (ti)**
This can be a rolling average of the latency for model mi over the last 5-10 minutes. Initially, we can assume ti = 1 second and update based on actual call latencies.

**Estimating Probability (pi)**
This is the hardest part and it will be handled by passing features to a Reality Check endpoint. Initially it can just be a random number generator sampling values from the uniform distribution on [0,1].

**Non-linear Time Penalties**
If you have strict SLAs (e.g., the user must get an answer in under 5 seconds), you might change the linear time penalty (βti) to an exponential one, or introduce a hard constraint where EU(mi) = -∞ if ti > 5.

Instead of using a local model, we can actually use the users' LLMs to judge whether the answer is correct or not by looking at the query, the answer and then the next query. If we can get a score or log probs fro, the model and the actual LLMs doing the query responses this can be turned in to features. More details need to be added here.

## Tool Integration

This Reality Code Rerouter can be used directly by editors like VS Codium and Zed or by agent systems like openclaw by pointing them to the /v1/completions or `/v1/chat/completions endpoints.

For VS Codium/VS Code:
```json
{
  "reality.rerouter.url": "http://localhost:3000/v1/completions"
}
```

For Zed Editor:
In Zed, you can configure multiple LLM providers by setting up the Reality Code Rerouter as your backend. The configuration file (config/config.json) allows you to add any LLM provider you want to use.

To configure Zed to use your Reality Code Rerouter:
1. Set up your LLM providers in config/config.json with their respective API keys as environment variables
2. Point Zed to your Reality Code Rerouter endpoint:
```json
{
  "reality.codeRerouter.url": "http://localhost:3000/v1/completions"
}
```

## Adding Custom LLM Providers

Security Considerations
For Self-Hosted Scenarios:
API keys are never stored in the configuration file. Instead, they are provided through environment variables:
- OPENAI_API_KEY for OpenAI
- ANTHROPIC_API_KEY for Anthropic
- HUGGINGFACE_API_KEY for Hugging Face
- CUSTOM_LLM_API_KEY for custom providers

This approach ensures that your API keys are never exposed in the codebase.

## Testing

Run tests with:
```bash
python -m unittest tests/test_router.py

## Error Handling and Logging

The system implements comprehensive error handling with:
- Graceful degradation when models are unavailable
- Detailed logging for debugging and monitoring
- Circuit breaker patterns for failed model connections
- Retry mechanisms with exponential backoff
- Standardized error responses following API conventions

All errors are logged to both console and database for debugging purposes.

## Deployment

The system is designed to be deployed to Azure and other cloud platforms using the provided Dockerfile and to be installed locally using a TUI setup similar to OpenCLAW.

## Data Storage for Debugging

For debugging and analytics purposes, the system will store routing data in a database. This data is not exposed to end users but is crucial for:

- Performance monitoring and optimization
- Routing decision analysis
- Model comparison and evaluation
- System behavior tracking
- Error analysis and troubleshooting

### Database Design

The system will use SQLite for local development and can be configured to use PostgreSQL or MySQL for production environments. The database will store:

1. **Routing Logs**:
   - Request metadata (timestamp, query, parameters)
   - Selected model information
   - Cost, time, and probability metrics
   - Outcome (correct/incorrect)

2. **Model Performance Metrics**:
   - Historical performance data
   - Cost tracking
   - Latency measurements
   - Success rates

3. **Configuration Data**:
   - Routing rules
   - Parameter settings
   - Model configurations

### Storage Strategy

- **Local Development**: SQLite database file stored in the project directory
- **Production**: Configurable database backend (PostgreSQL/MySQL)
- **Data Retention**: Configurable retention policies for historical data
- **Backup**: Automated backup mechanisms for critical data

This database approach ensures that developers can analyze system behavior and optimize routing decisions without impacting end-user experience.

## Project Setup

### setup.py
The project uses a standard Python setup.py file with:
- Package name: reality-router
- Version: 1.0.0
- Entry points for CLI tools
- Dependency management
- Installation scripts

## Source Code Structure

The source code is organized as follows:
```
src/
├── __init__.py
├── main.py              # Entry point
├── router/
│   ├── __init__.py
│   ├── core.py          # Core routing logic
│   ├── load_balancer.py # Load balancing algorithms
│   └── metrics.py       # Metrics collection
├── adapters/
│   ├── __init__.py
│   ├── openai_adapter.py
│   ├── anthropic_adapter.py
└── mistral_adapter.py
├── config/
│   ├── __init__.py
│   ├── settings.py      # Configuration management
│   └── routing_rules.py # Routing rules
└── utils/
    ├── __init__.py
    ├── logger.py        # Logging utilities
    └── helpers.py       # Helper functions
```