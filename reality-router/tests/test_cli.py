import os
import json
import sys

# Ensure start_router is importable by adding the parent of LLMRerouter/reality-router to sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PARENT_ROOT = os.path.abspath(os.path.join(PROJECT_ROOT, ".."))
if PARENT_ROOT not in sys.path:
    sys.path.insert(0, PARENT_ROOT)

import start_router
import pytest
from unittest.mock import MagicMock, patch, mock_open


@pytest.fixture(autouse=True)
def clean_test_env():
    """Ensure that the local environment points to the isolated test directory."""
    assert "REALITY_ROUTER_HOME" in os.environ, "Tests must run inside an isolated REALITY_ROUTER_HOME sandbox!"
    # Clear any potential real environment overrides
    for k in ["REALITY_CHECK_TOKEN", "DEFAULT_STRATEGY", "COST_SENSITIVITY", "TIME_SENSITIVITY", "SENTIMENT_MODEL_ID"]:
        if k in os.environ:
            del os.environ[k]
    yield


def test_cli_arg_parsing():
    """Verify that command-line argument parsing maps to the correct fields."""
    args = start_router.parse_args(["setup", "--agent", "--json"])
    assert args.command == "setup"
    assert args.agent is True
    assert args.json is True

    args = start_router.parse_args(["start", "--non-interactive", "--port", "9000", "--detach"])
    assert args.command == "start"
    assert args.non_interactive is True
    assert args.port == 9000
    assert args.detach is True

    args = start_router.parse_args(["status", "--strategy", "tiered_assessment"])
    assert args.command == "status"
    assert args.strategy == "tiered_assessment"

    args = start_router.parse_args(["doctor", "--cost-sensitivity", "0.8"])
    assert args.command == "doctor"
    assert args.cost_sensitivity == "0.8"


def test_complete_non_interactive_execution():
    """
    Ensure input() and inquirer are mocked to raise exceptions.
    Verify that in --non-interactive mode, no prompts are touched,
    and it crashes/exits with the stable non-zero exit codes when credentials are missing.
    """
    # Mock input and inquirer to raise exceptions if touched
    mock_input = MagicMock(side_effect=AssertionError("Prompt input() was called in non-interactive run!"))
    mock_inquirer = MagicMock(side_effect=AssertionError("Inquirer prompt was called in non-interactive run!"))

    args = start_router.parse_args(["setup", "--non-interactive"])
    config = {}

    with patch("builtins.input", mock_input), patch("start_router.inquirer", mock_inquirer):
        with pytest.raises(SystemExit) as exc_info:
            start_router.cmd_setup(args, config)
        assert exc_info.value.code == start_router.EXIT_NO_CREDENTIALS


def test_agent_mode_auto_setup_zero_prompts():
    """
    Verify that in --agent mode, the wizard runs completely with zero prompts,
    choosing sensible defaults and completing successfully.
    """
    mock_input = MagicMock(side_effect=AssertionError("input() prompted in agent mode!"))
    mock_inquirer = MagicMock(side_effect=AssertionError("inquirer prompted in agent mode!"))

    args = start_router.parse_args(["setup", "--agent", "--json"])
    config = {}

    # Mock get_all_models to return some mock models so discovery completes
    mock_models = [{"id": "gpt-4o", "name": "GPT-4o", "provider": "openai"}]

    with patch("builtins.input", mock_input), \
         patch("start_router.inquirer", mock_inquirer), \
         patch("start_router.get_all_models", return_value=mock_models), \
         patch("start_router.save_env") as mock_save:
        
        start_router.cmd_setup(args, config)
        
        # Verify that save_env was called to write the defaults
        assert mock_save.called
        assert config["SENTIMENT_MODEL_ID"] == "gpt-4o"
        assert config["REALITY_CHECK_TOKEN"] == "agent_auto_token"
        assert config["REALITY_CHECK_PROVIDER"] =="AgentAuto"


def test_cli_precedence_rules():
    """
    Verify precedence: CLI > Env > Config > Auto-detect > Fallbacks.
    """
    # 1. Config has base value
    with patch("start_router.load_env", return_value={"DEFAULT_STRATEGY": "expected_utility"}):
        # 2. Env overrides config
        with patch.dict(os.environ, {"DEFAULT_STRATEGY": "tiered_assessment"}):
            # No CLI argument
            args = start_router.parse_args(["setup"])
            config = start_router.resolve_config(args)
            assert config["DEFAULT_STRATEGY"] == "tiered_assessment"

            # 3. CLI overrides env
            args_cli = start_router.parse_args(["setup", "--strategy", "custom_strategy"])
            config_cli = start_router.resolve_config(args_cli)
            assert config_cli["DEFAULT_STRATEGY"] == "custom_strategy"


def test_proper_environment_provider_detection():
    """
    Verify that provider keys are detected from OS environment variables without printing values.
    """
    # Set mock environment variables
    mock_env = {
        "OPENAI_API_KEY": "sk-123456",
        "GEMINI_API_KEY": "gemini-secret"
    }
    with patch.dict(os.environ, mock_env):
        args = start_router.parse_args(["setup"])
        config = start_router.resolve_config(args)
        
        # Verify provider keys are loaded from env
        assert config["OPENAI_API_KEY"] == "sk-123456"
        assert config["GEMINI_API_KEY"] == "gemini-secret"


def test_status_command_json_output(capsys):
    """
    Verify that status command outputs a clean metadata JSON dictionary.
    """
    args = start_router.parse_args(["status", "--json"])
    config = {
        "SENTIMENT_MODEL_ID": "qwen3.6:35b",
        "REALITY_CHECK_TOKEN": "Bearer some_token",
        "REALITY_CHECK_PROVIDER": "Microsoft"
    }

    with patch("start_router.read_pid", return_value=None), \
         patch("start_router.port_is_free", return_value=True), \
         patch("start_router.router_is_serving", return_value=False), \
         patch("start_router.get_all_models", return_value=[]):
        start_router.cmd_status(args, config)
        
        captured = capsys.readouterr()
        status_data = json.loads(captured.out)
        
        assert status_data["status"] == "stopped"
        assert status_data["sentiment_model"] == "qwen3.6:35b"
        assert status_data["reality_signal"] is True
        assert status_data["models_total"] == 0


def test_doctor_command_exit_codes():
    """
    Verify that doctor command runs active checks and exits with appropriate codes.
    """
    args = start_router.parse_args(["doctor"])
    
    # 1. Test failed env/config check (no .env file)
    with patch("os.path.exists", return_value=False):
        with pytest.raises(SystemExit) as exc_info:
            start_router.cmd_doctor(args, {})
        assert exc_info.value.code == 10

    # 2. Test failed SSO token check
    with patch("os.path.exists", return_value=True):
        with pytest.raises(SystemExit) as exc_info:
            start_router.cmd_doctor(args, {})
        assert exc_info.value.code == 11

    # 3. Test failed provider check
    with patch("os.path.exists", return_value=True):
        with pytest.raises(SystemExit) as exc_info:
            start_router.cmd_doctor(args, {"REALITY_CHECK_TOKEN": "Bearer sso_token"})
        assert exc_info.value.code == 12


def test_device_code_auth_json_stream(capsys):
    """
    Verify that device flow auth outputs the expected JSON event streams during authentication.
    """
    args = start_router.parse_args(["auth", "--agent", "--json"])
    config = {}

    # Mock device code registration response
    mock_device_response = json.dumps({
        "verification_uri": "https://microsoft.com/devicelogin",
        "user_code": "ABC-DEF-GHI",
        "device_code": "devcode123",
        "interval": 1,
        "expires_in": 10
    }).encode()

    # Mock polling success response
    mock_poll_response = json.dumps({
        "access_token": "secret_access_token_never_printed",
        "id_token": "header.payload.signature"
    }).encode()

    # Sequence of responses: 1. Device registration, 2. Successful poll
    mock_urlopen = MagicMock()
    mock_urlopen.return_value.__enter__.return_value.read.side_effect = [mock_device_response, mock_poll_response]
    mock_urlopen.return_value.__enter__.return_value.status = 200

    with patch("urllib.request.urlopen", mock_urlopen), \
         patch("time.sleep", return_value=None), \
         patch("start_router.save_env") as mock_save:
        
        start_router.cmd_auth(args, config)
        
        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")
        
        # Verify we output NDJSON
        assert len(lines) == 2
        
        # Event 1: auth_required
        event_1 = json.loads(lines[0])
        assert event_1["event"] == "auth_required"
        assert event_1["verification_uri"] == "https://microsoft.com/devicelogin"
        assert event_1["user_code"] == "ABC-DEF-GHI"
        
        # Event 2: auth_success
        event_2 = json.loads(lines[1])
        assert event_2["event"] == "auth_success"
        
        # Ensure credentials/secrets/tokens are NEVER printed
        assert "secret_access_token_never_printed" not in captured.out
        assert "devcode123" not in captured.out
        assert mock_save.called
