"""
Configuration management for Reality Router system
"""

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
    custom_llm_base_url: Optional[str] = Field(default=None)
    custom_llm_api_key: Optional[str] = Field(default=None)

    # Model settings
    disabled_models: List[str] = Field(default_factory=list)
    sentiment_model_id: Optional[str] = Field(default=None)

    # Routing settings
    enable_auto_discovery: bool = Field(default=True)
    default_strategy: str = Field(default="expected_utility")
    reward: float = Field(default=1.0)
    cost_sensitivity: float = Field(default=0.5)
    time_sensitivity: float = Field(default=0.5)

    # Model settings
    models_config: Dict[str, Any] = Field(default_factory=dict)
    models_config_path: str = Field(default=os.path.join(APP_HOME, "user_models.json"))

    # Load balancing settings
    load_balancing_strategy: str = Field(default="weighted")


# Global settings instance
# Initialize with lowercase keys mapping from the manually loaded .env file
_settings_data = {k.lower(): v for k, v in _env_vars.items()}

# Parse comma-separated string for disabled_models if present
if "disabled_models" in _settings_data and isinstance(
    _settings_data["disabled_models"], str
):
    val = _settings_data["disabled_models"].strip()
    _settings_data["disabled_models"] = (
        [x.strip() for x in val.split(",")] if val else []
    )

settings = Settings(**_settings_data)


def get_settings() -> Settings:
    """Get application settings"""
    return settings


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
