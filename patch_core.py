import re

with open("llm-router/src/router/core.py", "r") as f:
    content = f.read()

# Replace the tool stripping logic
old_block = """                    has_tools = bool(request.parameters and request.parameters.get("tools"))
                    if has_tools and not self.models[decision.model_id].get("supports_function_calling"):
                        del request.parameters["tools"]
                        if "tool_choice" in request.parameters:
                            del request.parameters["tool_choice"]
                        sys_msg = {
                            "role": "system",
                            "content": "The user has MCP tools available. Please respond with a JSON object that matches the requested tool schema."
                        }
                        if request.parameters and "messages" in request.parameters:
                            request.parameters["messages"].insert(0, sys_msg)"""

new_block = """                    has_tools = bool(request.parameters and request.parameters.get("tools"))
                    if has_tools and not self.models[decision.model_id].get("supports_function_calling"):
                        import json
                        tools_schema = request.parameters["tools"]
                        del request.parameters["tools"]
                        if "tool_choice" in request.parameters:
                            del request.parameters["tool_choice"]
                        sys_msg = {
                            "role": "system",
                            "content": "The user has MCP tools available. Please respond with a JSON object that matches the following requested tool schemas:\\n" + json.dumps(tools_schema, indent=2)
                        }
                        if request.parameters and "messages" in request.parameters:
                            request.parameters["messages"].insert(0, sys_msg)"""

content = content.replace(old_block, new_block)

with open("llm-router/src/router/core.py", "w") as f:
    f.write(content)

print("Patched core.py")
