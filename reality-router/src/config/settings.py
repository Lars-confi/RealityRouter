"""
Configuration management for Reality Router system
"""

import json
import os
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def _load_env_file(path=".env"):
    """Strictly load only from the specified file, ignoring system environment"""
    env_vars = {}
    if os.path.exists(path):
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env_vars[k.strip()] = v.strip().strip("'").strip('"')
    return env_vars


# Manually load .env to avoid picking up global system environment variables
# Check current directory and parent directory for .env
APP_HOME = os.getenv("REALITY_ROUTER_HOME", os.path.expanduser("~/.reality_router"))
_env_path = os.path.join(APP_HOME, ".env")
_env_vars = _load_env_file(_env_path)


class Settings(BaseModel):
    """Application settings"""

    # Application settings
    app_name: str = Field(default="Reality Router")
    debug: bool = Field(default=False)

    # Database settings
    database_url: str = Field(
        default=f"sqlite:///{os.path.join(APP_HOME, 'reality_router.db')}"
    )

    # API keys
    openai_api_key: Optional[str] = Field(default=None)
    anthropic_api_key: Optional[str] = Field(default=None)
    mistral_api_key: Optional[str] = Field(default=None)
    gemini_api_key: Optional[str] = Field(default=None)
    deepseek_api_key: Optional[str] = Field(default=None)
    moonshot_api_key: Optional[str] = Field(default=None)
    zai_api_key: Optional[str] = Field(default=None)
    xai_api_key: Optional[str] = Field(default=None)
    dashscope_api_key: Optional[str] = Field(default=None)
    openrouter_api_key: Optional[str] = Field(default=None)
    # Comma-separated substrings, or "all". Empty means the gap-filler default:
    # only vendors you have no direct key for. See core._discover_openrouter.
    openrouter_models: Optional[str] = Field(default=None)
    # Caps how many models each OpenRouter vendor may contribute under the
    # gap-filler default. Ignored when OPENROUTER_MODELS is set.
    openrouter_max_per_vendor: int = Field(default=2)
    # $0 models are excluded by default because they win every routing decision
    # on cost and are heavily rate limited. Naming one in OPENROUTER_MODELS
    # already overrides that; this lifts it for the default and "all" modes.
    openrouter_allow_free: Optional[str] = Field(default=None)
    # LM Studio serves the OpenAI protocol locally; unset means "not in use".
    lm_studio_base_url: Optional[str] = Field(default=None)
    lm_studio_api_key: Optional[str] = Field(default=None)
    custom_llm_base_url: Optional[str] = Field(default=None)
    custom_llm_api_key: Optional[str] = Field(default=None)
    reality_check_token: Optional[str] = Field(default=None)

    # Inbound auth: keys clients must present. Empty means auth is off.
    router_api_keys: List[str] = Field(default_factory=list)
    reality_check_provider: Optional[str] = Field(default=None)
    # Google and Microsoft credentials last an hour; these let the router
    # refresh instead of silently falling back to an uncalibrated 0.5.
    reality_check_refresh_token: Optional[str] = Field(default=None)
    reality_check_token_expires_at: Optional[str] = Field(default=None)
    reality_routing_url: str = Field(
        default="https://snap-api.blackglacier-173a252d.swedencentral.azurecontainerapps.io"
    )
    reality_rerouting_url: str = Field(
        default="https://ladder-api.blackglacier-173a252d.swedencentral.azurecontainerapps.io"
    )

    # User Profile settings
    user_email: str = Field(default="anonymous")
    user_location: str = Field(default="unknown")
    sso_email: str = Field(default="anonymous")

    # Model settings
    disabled_models: List[str] = Field(default_factory=list)
    enabled_models: List[str] = Field(default_factory=list)
    model_preferences: Dict[str, float] = Field(default_factory=dict)
    sentiment_model_id: Optional[str] = Field(default=None)

    # Routing settings
    enable_auto_discovery: bool = Field(default=True)
    default_strategy: str = Field(default="expected_utility")
    enable_sticky_sessions: bool = Field(default=False)
    cost_sensitivity: float = Field(default=0.5)
    time_sensitivity: float = Field(default=0.5)
    agent_handling: str = Field(default="auto")
    has_enabled_models_allowlist: bool = Field(default=False)
    ladder_escalation_threshold: float = Field(default=10.0)

    # Model settings
    models_config: Dict[str, Any] = Field(default_factory=dict)
    models_config_path: str = Field(default=os.path.join(APP_HOME, "user_models.json"))

    # Load balancing settings
    load_balancing_strategy: str = Field(default="weighted")

    def is_model_enabled(self, model_id: str) -> bool:
        if model_id in self.disabled_models:
            return False
        pref = self.model_preferences.get(model_id)
        if pref is not None and pref <= 0.0:
            return False
        if self.has_enabled_models_allowlist or self.enabled_models:
            return model_id in self.enabled_models
        return True


def _parse_router_api_keys(data: Dict[str, Any]) -> None:
    """ROUTER_API_KEYS is comma-separated in .env; blanks are dropped."""
    val = data.get("router_api_keys")
    if isinstance(val, str):
        data["router_api_keys"] = [k.strip() for k in val.split(",") if k.strip()]


def _parse_models_state(data_dict: Dict[str, Any]) -> None:
    """Parse model activation state (.env JSON/DJ) with priority order"""
    # Priority 0: capture whether ENABLED_MODELS was explicitly set in .env
    data_dict["has_enabled_models_allowlist"] = ("enabled_models" in data_dict)
    # Priority 1: MODEL_PREFERENCES JSON dict (direct preferences)
    if "model_preferences" in data_dict and isinstance(data_dict["model_preferences"], str):
        import json
        try:
            data_dict["model_preferences"] = json.loads(data_dict["model_preferences"])
        except Exception:
            data_dict["model_preferences"] = {}
    
    # Priority 2: ENABLED_MODELS (comma-separated list of enabled models)
    enabled = set()
    enabled_models_str = data_dict.get("enabled_models", "")
    if enabled_models_str:
        enabled.update([x.strip() for x in enabled_models_str.split(",") if x.strip()])
    
    # Priority 3: DISABLED_MODELS (comma-separated list of disabled models)
    disabled = set()
    disabled_models_str = data_dict.get("disabled_models", "")
    if disabled_models_str:
        disabled.update([x.strip() for x in disabled_models_str.split(",") if x.strip()])
    
    # Priority 4: Fallback to disabled_models.json file (only if neither env vars nor preferences exist)
    disabled_json_path = os.path.join(APP_HOME, "disabled_models.json")
    if not (enabled_models_str or disabled_models_str or data_dict.get("model_preferences")) and os.path.exists(disabled_json_path):
        try:
            with open(disabled_json_path, "r") as f:
                json_data = json.load(f)
                if isinstance(json_data, list):
                    disabled.update(json_data)
        except Exception:
            pass
    
    # Reconcile preferences with enabled/disabled states FIRST
    model_prefs = data_dict.get("model_preferences", {})
    # If pref <= 0.0, add to disabled and discard from enabled
    for m, pref in model_prefs.items():
        if pref <= 0.0:
            disabled.add(m)
            enabled.discard(m)
    # Remove disabled from enabled
    enabled -= disabled
    
    # If enabled is non-empty, also add any m with model_prefs.get(m, 0.0) > 0.0 and not in disabled into enabled
    if enabled:
        for m, pref in model_prefs.items():
            if pref > 0.0 and m not in disabled:
                enabled.add(m)
    
    # Write out cleaned sets before processing preferences further
    data_dict["enabled_models"] = list(enabled)
    data_dict["disabled_models"] = list(disabled)

    # Ensure all enabled models have positive preferences
    for model in enabled:
        if model not in model_prefs or model_prefs[model] <= 0.0:
            model_prefs[model] = 100.0

    # Ensure all disabled models have zero or negative preferences
    for model in disabled:
        if model in model_prefs and model_prefs[model] > 0.0:
            model_prefs[model] = 0.0

    data_dict["model_preferences"] = model_prefs


def _parse_disabled_models(data_dict):
    _parse_models_state(data_dict)


# Global settings instance
# Initialize with lowercase keys mapping from the manually loaded .env file
_settings_data = {k.lower(): v for k, v in _env_vars.items()}
_parse_models_state(_settings_data)
_parse_router_api_keys(_settings_data)

settings = Settings(**_settings_data)


def get_settings() -> Settings:
    """Get application settings"""
    return settings


def reload_settings() -> Settings:
    """Reload settings from the environment and configuration files"""
    global settings, _env_vars, _settings_data
    _env_vars = _load_env_file(_env_path)
    _settings_data = {k.lower(): v for k, v in _env_vars.items()}
    _parse_models_state(_settings_data)
    _parse_router_api_keys(_settings_data)

    settings = Settings(**_settings_data)
    return settings


def update_model_preference(model_id: str, value: float) -> None:
    """Update a model's preference value in the configuration"""
    # Load and validate the new preference value
    from src.config.settings import get_settings
    
    # Get current settings
    settings = get_settings()
    
    # Update the preference
    settings.model_preferences[model_id] = value
    
    # Recalculate enabled/disabled models based on the new preference
    disabled_models = set(settings.disabled_models)
    enabled_models = set(settings.enabled_models)
    
    # Load env_vars before updating
    env_vars = _load_env_file(_env_path)
    has_allowlist = settings.has_enabled_models_allowlist or bool(settings.enabled_models) or ("ENABLED_MODELS" in env_vars)
    
    if value <= 0.0:
        # Add to disabled, remove from enabled
        disabled_models.add(model_id)
        enabled_models.discard(model_id)
    else:
        # For enabling models, check if we have an allowlist
        # If enabled_models was configured (not empty) or ENABLED_MODELS in .env,
        # then add to enabled when re-enabling a model
        if has_allowlist:
            enabled_models.add(model_id)
        disabled_models.discard(model_id)
    
    # Update settings with new states
    settings.model_preferences[model_id] = value
    settings.disabled_models = list(disabled_models)
    settings.enabled_models = list(enabled_models)
    
    # Save back to .env
    env_vars["DISABLED_MODELS"] = ",".join(settings.disabled_models)
    
    if has_allowlist:
        env_vars["ENABLED_MODELS"] = ",".join(sorted(settings.enabled_models))
    else:
        # Only set if previously had enabled models to maintain consistency
        pass
        
    env_vars["MODEL_PREFERENCES"] = json.dumps(settings.model_preferences)
    
    # Remove disabled_models.json if it exists
    disabled_json_path = os.path.join(APP_HOME, "disabled_models.json")
    if os.path.exists(disabled_json_path):
        try:
            os.remove(disabled_json_path)
        except Exception:
            pass
    
    # Write updated .env file
    with open(_env_path, "w") as f:
        for k, v in env_vars.items():
            f.write(f"{k}={v}\n")
    
    # Reload settings to pick up changes
    reload_settings()


def update_default_strategy(strategy: str) -> None:
    """Update default routing strategy in settings and save to .env"""
    valid = "tiered_assessment" if strategy in ("ladder", "tiered_assessment") else "expected_utility"
    settings.default_strategy = valid
    env_vars = _load_env_file(_env_path)
    env_vars["DEFAULT_STRATEGY"] = valid
    with open(_env_path, "w") as f:
        for k, v in env_vars.items():
            f.write(f"{k}={v}\n")
    reload_settings()


def load_models_from_config() -> Dict[str, Dict[str, Any]]:
    """Load model configurations from settings"""
    models = {}

    # Load from explicitly configured path or default
    import json

    config_path = settings.models_config_path
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                user_models = json.load(f)
                if user_models:
                    logger.info(f"Loaded models from {config_path}")
                    return user_models
        except Exception as e:
            logger.error(f"Failed to load user models from {config_path}: {e}")

    if settings.models_config:
        models.update(settings.models_config)

    logger.info(f"Loaded {len(models)} models from configuration")
    return models
