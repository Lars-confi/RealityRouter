"""
LiteLLM adapter for routing requests to various LLM providers using the litellm library.
"""

import json
import re
import time
from typing import Any, Dict, Optional

import litellm

from ..models.routing import RoutingRequest
from .base_adapter import BaseAdapter


class LiteLLMAdapter(BaseAdapter):
    """Adapter for routing requests via LiteLLM"""

    def __init__(
        self,
        model_name: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        """
        Initialize the LiteLLM adapter

        Args:
            model_name: Name of the model (LiteLLM format, e.g., 'gpt-4o', 'anthropic/claude-3-opus-20240229')
            api_key: API key for the provider
            base_url: Optional base URL for custom providers
        """
        super().__init__(model_name, api_key)
        self.base_url = base_url

    async def forward_request(self, request: RoutingRequest) -> Dict[str, Any]:
        """
        Forward a request to the LLM provider using LiteLLM

        Args:
            request: RoutingRequest to forward

        Returns:
            Normalized response dictionary
        """
        if not self.validate_request(request):
            raise ValueError("Invalid request format")

        params = request.parameters or {}

        # Prepare messages
        messages = params.get("messages")
        if not messages:
            messages = [{"role": "user", "content": request.query}]

        # Prepare LiteLLM arguments
        litellm_args = {
            "model": self.model_name,
            "messages": messages,
        }

        if self.api_key:
            litellm_args["api_key"] = self.api_key
        if self.base_url:
            litellm_args["base_url"] = self.base_url

        # Forward standard completion parameters
        allowed_params = [
            "temperature",
            "max_tokens",
            "max_completion_tokens",
            "top_p",
            "top_k",
            "tools",
            "tool_choice",
            "response_format",
            "stream",
            "frequency_penalty",
            "presence_penalty",
            "stop",
            "logprobs",
            "top_logprobs",
        ]

        for key in allowed_params:
            if key in params:
                litellm_args[key] = params[key]

        # Force stream=False internally for the router's logic to work correctly.
        # This prevents 'CustomStreamWrapper' attribute errors and allows the router
        # to assess responses before sending them back to the client.
        litellm_args["stream"] = False

        try:
            # Call LiteLLM async completion
            response = await litellm.acompletion(**litellm_args)

            # Extract main content
            choice = response.choices[0]
            message = choice.message
            text = getattr(message, "content", "") or ""

            # Extract reasoning/reasoning_content if main content is empty
            if not text:
                # 1. Check for reasoning attributes directly on message object
                for attr in ["reasoning_content", "reasoning", "thought", "refusal"]:
                    val = getattr(message, attr, None)
                    if val:
                        text = val
                        break

                # 2. Check for provider_specific_fields (DeepSeek/Nemotron/Ollama)
                if (
                    not text
                    and hasattr(message, "provider_specific_fields")
                    and isinstance(message.provider_specific_fields, dict)
                ):
                    psf = message.provider_specific_fields
                    text = (
                        psf.get("reasoning_content")
                        or psf.get("reasoning")
                        or psf.get("thought")
                        or psf.get("refusal")
                        or ""
                    )

            finish_reason = choice.finish_reason

            # Parse tool calls into the standard dictionary format expected by the router core
            tool_calls = []

            # 1. Handle standard structured tool calls
            if hasattr(message, "tool_calls") and message.tool_calls:
                for tc in message.tool_calls:
                    tool_calls.append(
                        {
                            "id": tc.id,
                            "type": tc.type,
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                    )

            # 2. Parse DeepSeek-style raw tags, JSON blocks, or other pseudo-tool calls
            if not tool_calls and text:
                try:
                    # Case A: DeepSeek tags (<｜tool call begin｜>...)
                    if (
                        "<｜tool call begin｜>" in text
                        or "<｜tool calls begin｜>" in text
                    ):
                        call_blocks = re.findall(
                            r"<｜tool call begin｜>(.*?)<｜tool call end｜>",
                            text,
                            re.DOTALL,
                        )
                        if not call_blocks and "<｜tool call begin｜>" in text:
                            call_blocks = [text.split("<｜tool call begin｜>")[-1]]

                        for i, block in enumerate(call_blocks):
                            parts = block.split("<｜tool sep｜>")
                            t_type, t_name, t_args = "function", "", "{}"
                            if len(parts) >= 3:
                                t_type, t_name, t_args = (
                                    parts[0].strip() or "function",
                                    parts[1].strip(),
                                    parts[2].strip(),
                                )
                            elif len(parts) == 2:
                                p1, p2 = parts[0].strip(), parts[1].strip()
                                if p1 == "function":
                                    t_name = p2
                                else:
                                    t_name, t_args = p1, p2
                            elif len(parts) == 1:
                                t_name = parts[0].strip()

                            if t_name:
                                # Clean name/args merger
                                name_match = re.search(
                                    r"^([a-zA-Z0-9_-]+)", t_name.strip()
                                )
                                if name_match:
                                    clean_name = name_match.group(1)
                                    remaining = t_name.strip()[
                                        len(clean_name) :
                                    ].strip()
                                    t_name = clean_name
                                    if remaining:
                                        t_args = (
                                            remaining
                                            if t_args == "{}"
                                            else f"{remaining}\n{t_args}"
                                        )

                                # Extract JSON from args
                                t_args = t_args.strip()
                                if not t_args.startswith("{"):
                                    t_args = re.sub(
                                        r"```[a-z]*\n?(.*?)\n?```",
                                        r"\1",
                                        t_args,
                                        flags=re.DOTALL,
                                    ).strip()
                                    if (
                                        t_name == "shell_execute"
                                        or t_name == "terminal"
                                    ):
                                        t_args = json.dumps({"command": t_args})
                                    elif t_name in [
                                        "python_execute",
                                        "python",
                                        "run_code",
                                    ]:
                                        t_args = json.dumps({"code": t_args})
                                    else:
                                        s, e = t_args.find("{"), t_args.rfind("}")
                                        if s != -1 and e != -1:
                                            t_args = t_args[s : e + 1]
                                        else:
                                            t_args = json.dumps({"arguments": t_args})

                                tool_calls.append(
                                    {
                                        "id": f"call_{i}_{int(time.time())}",
                                        "type": t_type,
                                        "function": {
                                            "name": t_name,
                                            "arguments": t_args,
                                        },
                                    }
                                )

                    # Case B: Standard Markdown JSON block (common for non-tagged models)
                    json_blocks = re.findall(
                        r"```json\s*\n?(.*?)\n?```", text, re.DOTALL
                    )
                    for i, block in enumerate(json_blocks):
                        try:
                            data = json.loads(block.strip())
                            # Recognize common formats: {"action": "name", "arguments": {...}} or {"name": "name", "parameters": {...}}
                            name = (
                                data.get("action")
                                or data.get("name")
                                or data.get("tool")
                                or data.get("function")
                            )
                            args = (
                                data.get("arguments")
                                or data.get("parameters")
                                or data.get("args")
                                or data.get("input")
                            )
                            if name:
                                tool_calls.append(
                                    {
                                        "id": f"json_call_{i}_{int(time.time())}",
                                        "type": "function",
                                        "function": {
                                            "name": str(name),
                                            "arguments": json.dumps(args)
                                            if isinstance(args, dict)
                                            else str(args),
                                        },
                                    }
                                )
                        except:
                            continue

                    # Case C: Bare JSON object at the end of text
                    if not tool_calls:
                        stripped_text = text.strip()
                        if stripped_text.startswith("{") and stripped_text.endswith(
                            "}"
                        ):
                            try:
                                data = json.loads(stripped_text)
                                name = data.get("action") or data.get("name")
                                args = data.get("arguments") or data.get("parameters")
                                if name:
                                    tool_calls.append(
                                        {
                                            "id": f"bare_call_{int(time.time())}",
                                            "type": "function",
                                            "function": {
                                                "name": str(name),
                                                "arguments": json.dumps(args)
                                                if isinstance(args, dict)
                                                else str(args),
                                            },
                                        }
                                    )
                            except:
                                pass

                    if tool_calls:
                        finish_reason = "tool_calls"
                        # Clean up text if possible (strip the blocks and tags)
                        text = re.sub(
                            r"<｜tool calls begin｜>.*", "", text, flags=re.DOTALL
                        )
                        text = re.sub(
                            r"```json.*```", "", text, flags=re.DOTALL
                        ).strip()
                        if not text:
                            text = ""
                except Exception as e:
                    import logging

                    logging.getLogger(__name__).error(
                        f"Heuristic tool parsing failed: {e}"
                    )

            # Extract usage statistics
            usage_dict = {}
            if hasattr(response, "usage") and response.usage:
                usage_dict = {
                    "prompt_tokens": getattr(response.usage, "prompt_tokens", 0),
                    "completion_tokens": getattr(
                        response.usage, "completion_tokens", 0
                    ),
                    "total_tokens": getattr(response.usage, "total_tokens", 0),
                }

            # Extract logprobs if available
            logprobs_data = {}
            try:
                if hasattr(choice, "logprobs") and choice.logprobs:
                    content_logprobs = choice.logprobs.content
                    if content_logprobs:
                        import math

                        import numpy as np

                        probs = [math.exp(token.logprob) for token in content_logprobs]
                        logprobs_list = [token.logprob for token in content_logprobs]

                        logprobs_data["logprobs_mean"] = (
                            float(np.mean(logprobs_list)) if logprobs_list else 0.0
                        )
                        logprobs_data["logprobs_std"] = (
                            float(np.std(logprobs_list)) if logprobs_list else 0.0
                        )

                        entropies = []
                        for token in content_logprobs:
                            if hasattr(token, "top_logprobs") and token.top_logprobs:
                                e = -sum(
                                    math.exp(t.logprob) * t.logprob
                                    for t in token.top_logprobs
                                )
                                entropies.append(e)

                        logprobs_data["entropy"] = (
                            float(np.mean(entropies)) if entropies else 0.0
                        )
                        logprobs_data["confidence"] = (
                            float(np.mean(probs)) if probs else 0.0
                        )

                        if len(logprobs_list) > 0:
                            logprobs_data["first_token_logprob"] = logprobs_list[0]
                        if len(logprobs_list) > 1:
                            logprobs_data["second_token_logprob"] = logprobs_list[1]
            except Exception:
                pass

            # Estimate cost using LiteLLM's built-in cost tracker
            try:
                cost = litellm.completion_cost(completion_response=response)
            except Exception:
                cost = 0.0

            result = {
                "text": text,
                "finish_reason": finish_reason,
                "tool_calls": tool_calls if tool_calls else None,
                "usage": usage_dict,
                "cost": cost,
                "raw_response": response.model_dump()
                if hasattr(response, "model_dump")
                else dict(response),
            }
            result.update(logprobs_data)
            return result

        except Exception as e:
            # Bubble up the exception to let the router load balancer / fallback logic handle it
            raise e

    def get_model_info(self) -> Dict[str, Any]:
        """
        Get information about the model
        """
        return {
            "model_name": self.model_name,
            "provider": "litellm",
            "base_url": self.base_url,
            "api_key_configured": bool(self.api_key),
        }
