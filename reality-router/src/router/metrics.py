"""
Metrics collection for LLM routing system
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.models.database import ModelPerformance, RoutingLog, get_db
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

router = APIRouter()


class MetricEntry(BaseModel):
    """Single metric entry"""

    timestamp: str
    model_id: str
    cost: float
    time: float
    probability: float
    success: bool
    query: str


class MetricsSummary(BaseModel):
    """Summary of metrics"""

    total_requests: int
    total_cost: float
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    average_cost: float
    average_time: float
    average_utility: float
    potential_max_cost: float
    success_rate: float
    models: Dict[str, Dict]
    agents: Dict[str, Dict]
    recent_events: List[Dict]
    timestamp: str


class ModelMetrics(BaseModel):
    """Model performance metrics"""

    model_id: str
    model_name: str
    total_requests: int
    total_cost: float
    average_time: float
    success_rate: float
    last_updated: str


class MetricsCollector:
    """Collects and manages routing metrics"""

    def __init__(self):
        """Initialize metrics collector"""
        self.metrics_storage = []

    def collect_routing_metrics(
        self,
        db: Session,
        model_id: str,
        model_name: str,
        expected_utility: float,
        cost: float,
        time: float,
        probability: float,
        success: bool,
        query: str,
        strategy: str = None,
        agent_id: str = "default",
        response_text: str = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        request_payload: str = None,
        response_payload: str = None,
        routing_context: str = None,
        confidence: float = None,
        entropy: float = None,
        logprobs_mean: float = None,
        logprobs_std: float = None,
        first_token_logprob: float = None,
        first_token_top_logprobs: str = None,
        second_token_logprob: float = None,
        second_token_top_logprobs: str = None,
    ):
        """
        Collect routing metrics for a single request
        """
        try:
            # Create a new routing log entry
            log_entry = RoutingLog(
                query=query,
                agent_id=agent_id,
                model_id=model_id,
                model_name=model_name,
                expected_utility=expected_utility,
                cost=cost,
                time=time,
                probability=probability,
                success=success,
                response_text=response_text,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                request_payload=request_payload,
                response_payload=response_payload,
                routing_context=routing_context,
                strategy=strategy,
                confidence=confidence,
                entropy=entropy,
                logprobs_mean=logprobs_mean,
                logprobs_std=logprobs_std,
                first_token_logprob=first_token_logprob,
                first_token_top_logprobs=first_token_top_logprobs,
                second_token_logprob=second_token_logprob,
                second_token_top_logprobs=second_token_top_logprobs,
            )

            db.add(log_entry)
            db.commit()
            db.refresh(log_entry)

            logger.info(f"Logged routing decision for model {model_id}")

            # Update model performance metrics
            self.update_model_performance(db, model_id, model_name, cost, time, success)

        except Exception as e:
            logger.error(f"Error collecting routing metrics: {str(e)}")

    def update_model_performance(
        self,
        db: Session,
        model_id: str,
        model_name: str,
        cost: float,
        time: float,
        success: bool,
    ):
        """
        Update performance metrics for a specific model

        Args:
            db: Database session
            model_id: ID of the model
            cost: Cost of the request
            time: Time taken for the request
            success: Whether the request was successful
        """
        try:
            # Try to find existing performance record
            perf_record = (
                db.query(ModelPerformance).filter_by(model_id=model_id).first()
            )

            if perf_record:
                # Update existing record
                perf_record.model_name = model_name
                perf_record.total_requests += 1
                perf_record.total_cost += cost
                perf_record.average_time = (
                    perf_record.average_time * (perf_record.total_requests - 1) + time
                ) / perf_record.total_requests
                perf_record.last_updated = datetime.utcnow()

                if success:
                    perf_record.success_rate = (
                        perf_record.success_rate * (perf_record.total_requests - 1) + 1
                    ) / perf_record.total_requests
                else:
                    perf_record.success_rate = (
                        perf_record.success_rate * (perf_record.total_requests - 1) + 0
                    ) / perf_record.total_requests
            else:
                # Create new record
                perf_record = ModelPerformance(
                    model_id=model_id,
                    model_name=model_name,
                    total_requests=1,
                    total_cost=cost,
                    average_time=time,
                    success_rate=1.0 if success else 0.0,
                )
                db.add(perf_record)

            db.commit()

        except Exception as e:
            logger.error(f"Error updating model performance: {str(e)}")


# Global metrics collector instance
metrics_collector = MetricsCollector()


@router.get("/summary")
async def get_metrics_summary(db: Session = Depends(get_db)):
    """
    Get summary of routing metrics from database

    Returns:
        MetricsSummary with aggregated statistics
    """
    try:
        # Get all routing logs from database
        logs = db.query(RoutingLog).all()

        if not logs:
            return MetricsSummary(
                total_requests=0,
                total_cost=0.0,
                total_prompt_tokens=0,
                total_completion_tokens=0,
                total_tokens=0,
                average_cost=0.0,
                average_time=0.0,
                average_utility=0.0,
                potential_max_cost=0.0,
                success_rate=0.0,
                models={},
                agents={},
                timestamp=datetime.utcnow().isoformat(),
            )

        # Calculate statistics
        total_requests = len(logs)
        total_cost = sum(log.cost for log in logs)
        total_prompt_tokens = sum(log.prompt_tokens or 0 for log in logs)
        total_completion_tokens = sum(log.completion_tokens or 0 for log in logs)
        total_tokens = sum(log.total_tokens or 0 for log in logs)
        total_time = sum(log.time for log in logs)
        success_count = sum(1 for log in logs if log.success)

        # Group by model and agent
        model_stats = {}
        agent_stats = {}
        for log in logs:
            # Model grouping
            model_id = log.model_id
            if model_id not in model_stats:
                model_stats[model_id] = {
                    "name": log.model_name,
                    "requests": 0,
                    "total_cost": 0.0,
                    "total_prompt_tokens": 0,
                    "total_completion_tokens": 0,
                    "total_tokens": 0,
                    "total_time": 0.0,
                    "success_count": 0,
                    "total_utility": 0.0,
                    "positive_feedback_count": 0,
                    "feedback_count": 0,
                    "raw_costs": [],
                    "raw_times": [],
                    "raw_probs": [],
                }
            model_stats[model_id]["requests"] += 1
            model_stats[model_id]["total_cost"] += log.cost
            model_stats[model_id]["total_prompt_tokens"] += log.prompt_tokens or 0
            model_stats[model_id]["total_completion_tokens"] += (
                log.completion_tokens or 0
            )
            model_stats[model_id]["total_tokens"] += log.total_tokens or 0
            model_stats[model_id]["total_time"] += log.time
            model_stats[model_id]["total_utility"] += log.expected_utility or 0.0
            model_stats[model_id]["raw_costs"].append(log.cost)
            model_stats[model_id]["raw_times"].append(log.time)
            model_stats[model_id]["raw_probs"].append(log.probability)
            if log.success:
                model_stats[model_id]["success_count"] += 1

            if log.user_sentiment:
                model_stats[model_id]["feedback_count"] += 1
                if log.user_sentiment == "happy":
                    model_stats[model_id]["positive_feedback_count"] += 1

            # Agent grouping
            a_id = log.agent_id or "default"
            if a_id not in agent_stats:
                agent_stats[a_id] = {
                    "requests": 0,
                    "total_cost": 0.0,
                    "total_tokens": 0,
                    "success_count": 0,
                }
            agent_stats[a_id]["requests"] += 1
            agent_stats[a_id]["total_cost"] += log.cost
            agent_stats[a_id]["total_tokens"] += log.total_tokens or 0
            if log.success:
                agent_stats[a_id]["success_count"] += 1

        import numpy as np

        for m_id, stats in model_stats.items():
            for key, stat_key in [
                ("raw_costs", "cost_stats"),
                ("raw_times", "time_stats"),
                ("raw_probs", "prob_stats"),
            ]:
                data = stats.pop(key)
                if data:
                    stats[stat_key] = {
                        "min": float(np.min(data)),
                        "q1": float(np.percentile(data, 25)),
                        "median": float(np.median(data)),
                        "q3": float(np.percentile(data, 75)),
                        "max": float(np.max(data)),
                    }
                else:
                    stats[stat_key] = {
                        "min": 0,
                        "q1": 0,
                        "median": 0,
                        "q3": 0,
                        "max": 0,
                    }

        # Calculate averages
        avg_cost = total_cost / total_requests if total_requests > 0 else 0.0
        avg_time = total_time / total_requests if total_requests > 0 else 0.0
        success_rate = success_count / total_requests if total_requests > 0 else 0.0

        # Calculate potential max cost based on the maximum cost available at the time of each request
        potential_max_cost = sum(
            (
                log.potential_cost
                if getattr(log, "potential_cost", None) is not None
                else log.cost
            )
            for log in logs
        )

        # Get recent events (last 5)
        recent_logs = (
            db.query(RoutingLog).order_by(RoutingLog.timestamp.desc()).limit(5).all()
        )
        recent_events = []
        for log in recent_logs:
            event = {
                "timestamp": log.timestamp.strftime("%Y-%m-%d %H:%M:%S")
                if log.timestamp
                else None,
                "success": log.success,
                "agent_id": log.agent_id or "default",
                "model_name": log.model_name,
                "model_id": log.model_id,
                "time": log.time,
                "cost": log.cost,
                "expected_utility": log.expected_utility or 0.0,
                "prompt_tokens": log.prompt_tokens or 0,
                "completion_tokens": log.completion_tokens or 0,
                "total_tokens": log.total_tokens or 0,
                "routing_context": json.loads(log.routing_context)
                if log.routing_context
                else [],
            }
            recent_events.append(event)

        return MetricsSummary(
            total_requests=total_requests,
            total_cost=total_cost,
            total_prompt_tokens=total_prompt_tokens,
            total_completion_tokens=total_completion_tokens,
            total_tokens=total_tokens,
            average_cost=avg_cost,
            average_time=avg_time,
            average_utility=0.0,
            potential_max_cost=potential_max_cost,
            success_rate=success_rate,
            models=model_stats,
            agents=agent_stats,
            recent_events=recent_events,
            timestamp=datetime.utcnow().isoformat(),
        )
    except Exception as e:
        logger.error(f"Error getting metrics summary: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error retrieving metrics: {str(e)}"
        )


@router.get("/models")
async def get_model_metrics(db: Session = Depends(get_db)):
    """
    Get metrics for all models

    Returns:
        List of model metrics
    """
    try:
        # Get all model performance records
        perf_records = db.query(ModelPerformance).all()

        model_metrics = []
        for record in perf_records:
            model_metrics.append(
                {
                    "model_id": record.model_id,
                    "model_name": record.model_name,
                    "total_requests": record.total_requests,
                    "total_cost": record.total_cost,
                    "average_time": record.average_time,
                    "success_rate": record.success_rate,
                    "last_updated": record.last_updated.isoformat()
                    if record.last_updated
                    else None,
                }
            )

        return model_metrics
    except Exception as e:
        logger.error(f"Error getting model metrics: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error retrieving model metrics: {str(e)}"
        )


@router.get("/history")
async def get_metrics_history():
    """
    Get complete history of routing metrics

    Returns:
        List of all metric entries
    """
    return metrics_collector.metrics_storage


@router.post("/log")
async def log_metric(entry: MetricEntry, db: Session = Depends(get_db)):
    """
    Log a metric entry to database

    Args:
        entry: MetricEntry to log
        db: Database session

    Returns:
        Success confirmation
    """
    try:
        # Convert MetricEntry to RoutingLog model
        db_entry = RoutingLog(
            timestamp=datetime.fromisoformat(entry.timestamp),
            model_id=entry.model_id,
            cost=entry.cost,
            time=entry.time,
            probability=entry.probability,
            success=entry.success,
            query=entry.query,
        )

        db.add(db_entry)
        db.commit()
        db.refresh(db_entry)

        logger.info(f"Logged metric for model {entry.model_id}")
        return {
            "status": "success",
            "message": "Metric logged successfully",
            "id": db_entry.id,
        }
    except Exception as e:
        logger.error(f"Error logging metric: {str(e)}")
        return {"status": "error", "message": str(e)}


class PreferenceUpdate(BaseModel):
    value: int


@router.get("/preferences")
async def get_preferences():
    from src.router.core import router_core

    calc = router_core.utility_calculator
    alpha = calc.cost_sensitivity
    beta = calc.time_sensitivity

    if alpha >= beta:
        # Left side or middle: beta is lower (or equal)
        # alpha/beta is between 1 and 100
        ratio = alpha / max(beta, 1e-6)
        # Clamp ratio to [1, 100]
        ratio = max(1.0, min(100.0, ratio))
        # v=0 -> ratio=100; v=50 -> ratio=1
        value = 50 * (100 - ratio) / 99
    else:
        # Right side: alpha is lower
        ratio = beta / max(alpha, 1e-6)
        ratio = max(1.0, min(100.0, ratio))
        # v=50 -> ratio=1; v=100 -> ratio=100
        value = 50 + 50 * (ratio - 1) / 99

    return {"value": int(value)}


@router.post("/preferences")
async def update_preferences(pref: PreferenceUpdate):
    val = max(0, min(100, pref.value))

    if val <= 50:
        # Left side: cost/preference priority
        # ratio 100:1 at 0, 1:1 at 50
        ratio = 100 - (99 * val / 50.0)
        alpha = ratio
        beta = 1.0
    else:
        # Right side: time priority
        # ratio 1:1 at 50, 1:100 at 100
        ratio = 1 + (99 * (val - 50) / 50.0)
        alpha = 1.0
        beta = ratio

    from src.router.core import router_core

    if hasattr(router_core, "utility_calculator"):
        router_core.utility_calculator.cost_sensitivity = alpha
        router_core.utility_calculator.time_sensitivity = beta

    return {"status": "success", "alpha": alpha, "beta": beta}


@router.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard():
    """
    Get a simple HTML dashboard for routing metrics
    """
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Reality Router Dashboard</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 2em; background: #121212; color: #e0e0e0; }
            .card { background: #1e1e1e; padding: 20px; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); margin-bottom: 30px; }
            h1 { color: #ecf0f1; margin-bottom: 0.5em; }
            h2 { color: #bdc3c7; border-bottom: 2px solid #333; padding-bottom: 10px; margin-top: 0; }
            .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 20px; }
            .stat { background: #2c2c2c; padding: 15px; border-radius: 8px; border-left: 4px solid #3498db; }
            .stat-savings { border-left-color: #2ecc71; background: #1a3b2b; }
            .stat-expense { border-left-color: #e67e22; background: #3a2518; }
            .stat-potential { border-left-color: #e74c3c; background: #3b1a1a; }
            .stat-value { font-size: 1.8em; font-weight: bold; color: #5dade2; margin: 5px 0; }
            .stat-savings .stat-value { color: #2ecc71; }
            .stat-expense .stat-value { color: #e67e22; }
            .stat-potential .stat-value { color: #e74c3c; }
            .stat-label { color: #95a5a6; font-size: 0.85em; text-transform: uppercase; letter-spacing: 1px; }
            table { width: 100%; border-collapse: collapse; margin-top: 15px; }
            th, td { text-align: left; padding: 12px 15px; border-bottom: 1px solid #333; }
            th { background: #2c2c2c; color: #bdc3c7; font-weight: 600; text-transform: uppercase; font-size: 0.75em; }
            tr:last-child td { border-bottom: none; }
            tr:hover { background: #2a2a2a; }
            .badge { padding: 4px 8px; border-radius: 4px; font-size: 0.8em; font-weight: 600; }
            .badge-success { background: #064e3b; color: #34d399; }
            .boxplot-container { width: 120px; height: 18px; position: relative; margin-top: 6px; background: rgba(255,255,255,0.03); border-radius: 2px; }
            .boxplot-whisker { position: absolute; height: 2px; background: #7f8c8d; top: 8px; }
            .boxplot-box { position: absolute; height: 10px; background: #3498db; top: 4px; opacity: 0.8; }
            .boxplot-median { position: absolute; height: 14px; width: 2px; background: #fff; top: 2px; }
            .dashboard-row { display: flex; gap: 20px; margin-bottom: 30px; }
            .dashboard-row .card { flex: 1; min-width: 0; margin-bottom: 0; display: flex; flex-direction: column; max-height: 600px; }
            .table-container { overflow: auto; flex-grow: 1; }
            @media (max-width: 1100px) { .dashboard-row { flex-direction: column; } .dashboard-row .card { max-height: none; } }
        </style>
    </head>
    <body>
        <div style="max-width: 1200px; margin: 0 auto;">
            <h1>Reality Router Control Center</h1>

            <div id="preferences" class="card">
                <h2>Routing Preferences</h2>
                <div style="display: flex; align-items: center; justify-content: space-between; margin-top: 20px;">
                    <div style="font-weight: bold; color: #e67e22; width: 140px; text-align: right;">Preference & Cost</div>
                    <div style="flex-grow: 1; margin: 0 20px; text-align: center;">
                        <input type="range" id="pref-slider" min="0" max="100" value="50" style="width: 100%; cursor: pointer;">
                        <div style="display: flex; justify-content: space-between; font-size: 0.8em; color: #7f8c8d; margin-top: 8px;">
                            <span>100:1</span>
                            <span>1:1</span>
                            <span>1:100</span>
                        </div>
                    </div>
                    <div style="font-weight: bold; color: #e74c3c; width: 140px; text-align: left;">Time</div>
                </div>
                <div id="pref-status" style="text-align: center; font-size: 0.85em; color: #3498db; margin-top: 10px; height: 1em;"></div>
            </div>

            <div id="summary" class="card">
                <h2>System Health & Usage</h2>
                <div class="grid" id="summary-grid">
                    <div class="stat">Loading stats...</div>
                </div>
            </div>

            <div id="models" class="card">
                <h2>Model Performance & Unit Economics</h2>
                <div class="table-container">
                    <table id="models-table">
                        <thead>
                            <tr>
                                <th>Intelligence Provider</th>
                                <th>Calls</th>
                                <th>Positive Feedback</th>
                                <th>Total Cost & Dist</th>
                                <th>Avg Latency & Dist</th>
                                <th>Throughput (Tokens)</th>
                                <th>Avg Utility & Prob Dist</th>
                            </tr>
                        </thead>
                        <tbody id="models-body"></tbody>
                    </table>
                </div>
                <div style="text-align: right; margin-top: 10px; font-size: 0.8em; color: #7f8c8d;">
                    *Pricing data provided by <a href="https://github.com/BerriAI/litellm" target="_blank" style="color: #3498db; text-decoration: none;">LiteLLM</a>
                </div>
            </div>

            <div class="dashboard-row">
                <div id="agents" class="card">
                    <h2>Agent Activity</h2>
                    <div class="table-container">
                        <table>
                            <thead>
                                <tr>
                                    <th>Agent ID</th>
                                    <th>Requests</th>
                                    <th>Total Cost</th>
                                    <th>Total Tokens</th>
                                    <th>Success Rate</th>
                                </tr>
                            </thead>
                            <tbody id="agents-body"></tbody>
                        </table>
                    </div>
                </div>

                <div id="recent-events" class="card">
                    <h2>Recent Events Trace</h2>
                    <div class="table-container" id="events-container">
                        <!-- Events will be injected here -->
                    </div>
                </div>
            </div>
        </div>

        <script>
            // Load and handle preferences
            async function initPreferences() {
                const slider = document.getElementById('pref-slider');
                const statusEl = document.getElementById('pref-status');

                try {
                    const res = await fetch('/metrics/preferences');
                    const data = await res.json();
                    if (data && data.value !== undefined) {
                        slider.value = data.value;
                    }
                } catch (e) { console.error("Failed to load preferences", e); }

                let timeout = null;
                slider.addEventListener('input', () => {
                    statusEl.innerText = "Pending update...";
                    clearTimeout(timeout);
                    timeout = setTimeout(async () => {
                        statusEl.innerText = "Saving...";
                        try {
                            const res = await fetch('/metrics/preferences', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ value: parseInt(slider.value) })
                            });
                            if (res.ok) {
                                statusEl.innerText = "Preferences updated successfully!";
                                setTimeout(() => statusEl.innerText = "", 2000);
                            } else {
                                statusEl.innerText = "Failed to update.";
                            }
                        } catch (err) {
                            statusEl.innerText = "Error communicating with server.";
                        }
                    }, 300);
                });
            }
            initPreferences();

            async function loadData() {
                try {
                    const res = await fetch('/metrics/summary');
                    const data = await res.json();

                    // Update Summary Grid
                    const summaryGrid = document.getElementById('summary-grid');
                    const savings = Math.max(0, data.potential_max_cost - data.total_cost);

                    let bestProbModel = "N/A";
                    let bestProbVal = -1;
                    let bestCostModel = "N/A";
                    let bestCostVal = Infinity;
                    let bestTimeModel = "N/A";
                    let bestTimeVal = Infinity;

                    let leastProbModel = "N/A";
                    let leastProbVal = Infinity;
                    let chattiestModel = "N/A";
                    let chattiestVal = -1;
                    let shyestModel = "N/A";
                    let shyestVal = Infinity;
                    let confusedModel = "N/A";
                    let confusedVal = -1;

                    for (const [id, stats] of Object.entries(data.models)) {
                        if (stats.requests === 0) continue;
                        let name = stats.name || id;

                        if (stats.prob_stats && stats.prob_stats.median > bestProbVal) {
                            bestProbVal = stats.prob_stats.median;
                            bestProbModel = name;
                        }
                        if (stats.prob_stats && stats.prob_stats.median < leastProbVal) {
                            leastProbVal = stats.prob_stats.median;
                            leastProbModel = name;
                        }
                        if (stats.cost_stats && stats.cost_stats.median < bestCostVal) {
                            bestCostVal = stats.cost_stats.median;
                            bestCostModel = name;
                        }
                        if (stats.time_stats && stats.time_stats.median < bestTimeVal) {
                            bestTimeVal = stats.time_stats.median;
                            bestTimeModel = name;
                        }

                        let avgTokens = stats.total_completion_tokens / stats.requests;
                        if (avgTokens > chattiestVal) {
                            chattiestVal = avgTokens;
                            chattiestModel = name;
                        }
                        if (avgTokens < shyestVal) {
                            shyestVal = avgTokens;
                            shyestModel = name;
                        }

                        let errorRate = 1.0 - (stats.success_count / stats.requests);
                        if (errorRate > confusedVal) {
                            confusedVal = errorRate;
                            confusedModel = name;
                        }
                    }
                    if (bestCostVal === Infinity) bestCostVal = 0;
                    if (bestTimeVal === Infinity) bestTimeVal = 0;
                    if (bestProbVal === -1) bestProbVal = 0;
                    if (leastProbVal === Infinity) leastProbVal = 0;
                    if (chattiestVal === -1) chattiestVal = 0;
                    if (shyestVal === Infinity) shyestVal = 0;
                    if (confusedVal === -1) confusedVal = 0;

                    summaryGrid.innerHTML = `
                        <div class="stat"><div class="stat-label">Total Volume</div><div class="stat-value">${data.total_requests.toLocaleString()}</div><div class="stat-label">Requests</div></div>
                        <div class="stat stat-expense"><div class="stat-label">Accrued Expense</div><div class="stat-value">$${data.total_cost.toFixed(2)}</div><div class="stat-label">Actual USD</div></div>
                        <div class="stat stat-potential"><div class="stat-label">Potential Cost</div><div class="stat-value">$${data.potential_max_cost.toFixed(2)}</div><div class="stat-label">Max Model USD</div></div>
                        <div class="stat stat-savings"><div class="stat-label">Total Savings</div><div class="stat-value">$${savings.toFixed(2)}</div><div class="stat-label">Retained Value</div></div>
                        <div class="stat"><div class="stat-label">Success Density</div><div class="stat-value">${(data.success_rate * 100).toFixed(1)}%</div><div class="stat-label">Operational</div></div>
                        <div class="stat" style="border-left-color: #9b59b6; background: #2c1e36;"><div class="stat-label">Most Reliable</div><div class="stat-value" style="font-size: 1.2em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #d2b4de;" title="${bestProbModel}">${bestProbModel}</div><div class="stat-label">${bestProbVal.toFixed(2)} (Med Prob)</div></div>
                        <div class="stat" style="border-left-color: #f1c40f; background: #332b10;"><div class="stat-label">Most Economical</div><div class="stat-value" style="font-size: 1.2em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #f9e79f;" title="${bestCostModel}">${bestCostModel}</div><div class="stat-label">$${bestCostVal.toFixed(2)} (Med Cost)</div></div>
                        <div class="stat" style="border-left-color: #1abc9c; background: #16332d;"><div class="stat-label">Fastest Response</div><div class="stat-value" style="font-size: 1.2em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #a3e4d7;" title="${bestTimeModel}">${bestTimeModel}</div><div class="stat-label">${bestTimeVal.toFixed(2)}s (Med Time)</div></div>
                        <div class="stat" style="border-left-color: #7f8c8d; background: #2b2b2b;"><div class="stat-label">Least Reliable</div><div class="stat-value" style="font-size: 1.2em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #bdc3c7;" title="${leastProbModel}">${leastProbModel}</div><div class="stat-label">${leastProbVal.toFixed(2)} (Med Prob)</div></div>
                        <div class="stat" style="border-left-color: #e67e22; background: #3d220f;"><div class="stat-label">Chattiest</div><div class="stat-value" style="font-size: 1.2em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #f5b041;" title="${chattiestModel}">${chattiestModel}</div><div class="stat-label">${chattiestVal.toFixed(0)} (Avg Tokens)</div></div>
                        <div class="stat" style="border-left-color: #3498db; background: #152b3c;"><div class="stat-label">Most Shy</div><div class="stat-value" style="font-size: 1.2em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #85c1e9;" title="${shyestModel}">${shyestModel}</div><div class="stat-label">${shyestVal.toFixed(0)} (Avg Tokens)</div></div>
                        <div class="stat" style="border-left-color: #e74c3c; background: #3b1a1a;"><div class="stat-label">Clumsiest Model</div><div class="stat-value" style="font-size: 1.2em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #f1948a;" title="${confusedModel}">${confusedModel}</div><div class="stat-label">${(confusedVal * 100).toFixed(1)}% (Error Rate)</div></div>
                    `;

                    // Update Agents Table
                    const agentsBody = document.getElementById('agents-body');
                    agentsBody.innerHTML = '';
                    const sortedAgents = Object.entries(data.agents).sort((a, b) => b[1].requests - a[1].requests);
                    for (const [id, stats] of sortedAgents) {
                        const row = document.createElement('tr');
                        const rate = ((stats.success_count / stats.requests) * 100).toFixed(1);
                        row.innerHTML = `
                            <td style="font-weight: 600;">${id}</td>
                            <td>${stats.requests.toLocaleString()}</td>
                            <td>$${stats.total_cost.toFixed(2)}</td>
                            <td>${stats.total_tokens.toLocaleString()}</td>
                            <td><span class="badge ${rate > 90 ? 'badge-success' : ''}">${rate}%</span></td>
                        `;
                        agentsBody.appendChild(row);
                    }

                    // Update Models Table
                    const modelsBody = document.getElementById('models-body');
                    modelsBody.innerHTML = '';

                    const sortedModels = Object.entries(data.models).sort((a, b) => b[1].requests - a[1].requests);

                    let maxCost = 0;
                    let maxTime = 0;
                    for (const [id, stats] of sortedModels) {
                        if (stats.cost_stats && stats.cost_stats.max > maxCost) maxCost = stats.cost_stats.max;
                        if (stats.time_stats && stats.time_stats.max > maxTime) maxTime = stats.time_stats.max;
                    }
                    if (maxCost === 0) maxCost = 1;
                    if (maxTime === 0) maxTime = 1;

                    function makeBoxplot(stats, globalMax, isProb) {
                        if (!stats) return '';
                        let min = stats.min, q1 = stats.q1, med = stats.median, q3 = stats.q3, max = stats.max;
                        let scale = globalMax;
                        if (isProb) { scale = 1.0; }

                        let pMin = (min / scale) * 100;
                        let pQ1 = (q1 / scale) * 100;
                        let pMed = (med / scale) * 100;
                        let pQ3 = (q3 / scale) * 100;
                        let pMax = (max / scale) * 100;

                        return `
                        <div class="boxplot-container" title="Min: ${min.toFixed(2)}&#10;Q1: ${q1.toFixed(2)}&#10;Median: ${med.toFixed(2)}&#10;Q3: ${q3.toFixed(2)}&#10;Max: ${max.toFixed(2)}">
                            <div class="boxplot-whisker" style="left: ${pMin}%; width: ${pMax - pMin}%;"></div>
                            <div class="boxplot-box" style="left: ${pQ1}%; width: ${Math.max(0.5, pQ3 - pQ1)}%;"></div>
                            <div class="boxplot-median" style="left: ${pMed}%;"></div>
                        </div>`;
                    }

                    for (const [id, stats] of sortedModels) {
                        const row = document.createElement('tr');
                        const avgUtil = stats.requests ? (stats.total_utility / stats.requests) : 0;
                        row.innerHTML = `
                            <td>
                                <div style="font-weight: 600; color: #ecf0f1;">${stats.name || id}</div>
                                <div style="font-size: 0.75em; color: #95a5a6;">${id}</div>
                            </td>
                            <td>${stats.requests.toLocaleString()}</td>
                            <td>
                                <div style="font-weight: 600; color: ${stats.feedback_count > 0 && stats.positive_feedback_count / stats.feedback_count >= 0.5 ? '#2ecc71' : (stats.feedback_count > 0 ? '#e74c3c' : '#95a5a6')};">
                                    ${stats.feedback_count ? Math.round((stats.positive_feedback_count / stats.feedback_count) * 100) + '%' : 'N/A'}
                                </div>
                                <div style="font-size: 0.75em; color: #95a5a6;">${stats.positive_feedback_count} / ${stats.feedback_count}</div>
                            </td>
                            <td>
                                <div>$${stats.total_cost.toFixed(2)}</div>
                                ${makeBoxplot(stats.cost_stats, maxCost, false)}
                            </td>
                            <td>
                                <div>${stats.total_time ? (stats.total_time / stats.requests).toFixed(2) + 's' : 'N/A'}</div>
                                ${makeBoxplot(stats.time_stats, maxTime, false)}
                            </td>
                            <td>
                                <div style="font-size: 0.85em;">P: ${stats.total_prompt_tokens.toLocaleString()}</div>
                                <div style="font-size: 0.85em;">C: ${stats.total_completion_tokens.toLocaleString()}</div>
                            </td>
                            <td>
                                <div style="margin-bottom: 4px;"><span class="badge badge-success">${avgUtil.toFixed(2)}</span></div>
                                ${makeBoxplot(stats.prob_stats, 1.0, true)}
                            </td>
                        `;
                        modelsBody.appendChild(row);
                    }

                    // Update Recent Events
                    const eventsContainer = document.getElementById('events-container');
                    eventsContainer.innerHTML = '';

                    if (data.recent_events && data.recent_events.length > 0) {
                        for (const event of data.recent_events) {
                            const eventDiv = document.createElement('div');
                            eventDiv.style.marginBottom = '30px';
                            eventDiv.style.padding = '20px';
                            eventDiv.style.border = '1px solid #444';
                            eventDiv.style.borderRadius = '8px';
                            eventDiv.style.background = '#111';
                            eventDiv.style.fontFamily = "'Courier New', Courier, monospace";

                            let html = `
                                <div style="color: #555; text-align: center; margin-bottom: 4px;">=====================================================</div>
                                <div style="color: #fff; text-align: center; font-weight: bold; margin-bottom: 4px; letter-spacing: 2px;">Event Detail View</div>
                                <div style="color: #555; text-align: center; margin-bottom: 12px;">=====================================================</div>

                                <div style="font-size: 0.9em; line-height: 1.4; margin-bottom: 20px; color: #ccc;">
                                    <strong>Timestamp:</strong> ${event.timestamp}<br>
                                    <strong>Status:</strong>    <span style="color: ${event.success ? '#2ecc71' : '#e74c3c'};">${event.success ? '✅ SUCCESS' : '❌ FAILED'}</span><br>
                                    <strong>Agent:</strong>     ${event.agent_id}<br>
                                    <strong>Model:</strong>     ${event.model_name} (${event.model_id})<br>
                                    <strong>Metrics:</strong>   Time: ${event.time.toFixed(2)}s | Cost: $${event.cost.toFixed(6)} | Utility: ${event.expected_utility.toFixed(4)}<br>
                                    <strong>Tokens:</strong>    ${event.prompt_tokens} prompt + ${event.completion_tokens} completion = ${event.total_tokens} total
                                </div>

                                <div style="color: #5dade2; font-weight: bold; margin-bottom: 8px;">[MODEL COMPARISON]</div>
                                <table style="font-size: 0.85em; width: 100%; border-collapse: collapse;">
                                    <thead>
                                        <tr style="border-bottom: 1px solid #333; color: #95a5a6;">
                                            <th style="text-align: left; padding: 4px;">Model Name</th>
                                            <th style="text-align: right; padding: 4px;">Utility</th>
                                            <th style="text-align: right; padding: 4px;">Prob</th>
                                            <th style="text-align: right; padding: 4px;">Cost</th>
                                            <th style="text-align: right; padding: 4px;">Time</th>
                                            <th style="text-align: right; padding: 4px;">Uncert</th>
                                            <th style="text-align: center; padding: 4px;">FB</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                            `;

                            if (event.routing_context && event.routing_context.length > 0) {
                                for (const d of event.routing_context) {
                                    const isSelected = d.model_id === event.model_id;
                                    html += `
                                        <tr style="${isSelected ? 'color: #fff; font-weight: bold;' : 'color: #777;'}">
                                            <td style="padding: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 140px;">
                                                ${isSelected ? '>> ' : '   '}${d.name || d.model_id}
                                            </td>
                                            <td style="padding: 4px; text-align: right;">${d.expected_utility.toFixed(4)}</td>
                                            <td style="padding: 4px; text-align: right;">${d.probability.toFixed(2)}</td>
                                            <td style="padding: 4px; text-align: right;">$${(d.cost || 0).toFixed(4)}</td>
                                            <td style="padding: 4px; text-align: right;">${(d.time || 0).toFixed(1)}s</td>
                                            <td style="padding: 4px; text-align: right;">${(d.uncertainty || 0).toFixed(2)}</td>
                                            <td style="padding: 4px; text-align: center;">${d.feedback_required ? '❌' : ''}</td>
                                        </tr>
                                    `;
                                }
                            } else {
                                html += '<tr><td colspan="7" style="text-align: center; color: #555; padding: 10px;">(No pool context available)</td></tr>';
                            }

                            html += `
                                    </tbody>
                                </table>
                                <div style="color: #444; margin-top: 10px;">----------------------------------------------------------------------</div>
                            `;
                            eventDiv.innerHTML = html;
                            eventsContainer.appendChild(eventDiv);
                        }
                    } else {
                        eventsContainer.innerHTML = '<div style="padding: 20px; text-align: center; color: #7f8c8d;">No events recorded in this session...</div>';
                    }
                } catch (e) {
                    console.error("Dashboard sync failed", e);
                }
            }
            loadData();
            setInterval(loadData, 5000);
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)
