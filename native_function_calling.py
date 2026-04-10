import json
from typing import Any, Dict, List, Optional


SUPPORTED_PROVIDERS = {"openai", "qwen", "deepseek", "gemini"}


class NativeFunctionCallingAdapter:
    """将 MCP Host 的工具注册表转换为不同模型厂商的原生 function calling 结构。"""

    @staticmethod
    def normalize_provider(provider: str) -> str:
        p = (provider or "").strip().lower()
        if p not in SUPPORTED_PROVIDERS:
            raise ValueError(f"unsupported provider: {provider}")
        return p

    @staticmethod
    def _normalize_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
        params = {}
        if isinstance(schema, dict):
            candidate = schema.get("inputSchema")
            if isinstance(candidate, dict):
                params = candidate
        if not isinstance(params, dict) or not params:
            params = {"type": "object", "properties": {}}

        if not isinstance(params.get("type"), str):
            params["type"] = "object"
        if not isinstance(params.get("properties"), dict):
            params["properties"] = {}
        if "required" in params and not isinstance(params.get("required"), list):
            params["required"] = []
        return params

    @staticmethod
    def _to_openai_tools(registry: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        for tool_name, meta in registry.items():
            schema = (meta or {}).get("schema") or {}
            description = schema.get("description") or schema.get("summary") or schema.get("note") or ""
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": description,
                        "parameters": NativeFunctionCallingAdapter._normalize_schema(schema),
                    },
                }
            )
        return tools

    @staticmethod
    def _to_gemini_tools(registry: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        declarations: List[Dict[str, Any]] = []
        for tool_name, meta in registry.items():
            schema = (meta or {}).get("schema") or {}
            description = schema.get("description") or schema.get("summary") or schema.get("note") or ""
            declarations.append(
                {
                    "name": tool_name,
                    "description": description,
                    "parameters": NativeFunctionCallingAdapter._normalize_schema(schema),
                }
            )
        return [{"functionDeclarations": declarations}] if declarations else []

    @staticmethod
    def build_tools_payload(provider: str, registry: Dict[str, Dict[str, Any]]) -> Any:
        p = NativeFunctionCallingAdapter.normalize_provider(provider)
        if p in {"openai", "qwen", "deepseek"}:
            return NativeFunctionCallingAdapter._to_openai_tools(registry)
        return NativeFunctionCallingAdapter._to_gemini_tools(registry)

    @staticmethod
    def parse_tool_calls(provider: str, response: Any) -> List[Dict[str, Any]]:
        """
        将模型响应标准化为 MCP Host 可执行的 spec 列表:
        [{"id": "call_x", "name": "tool_name", "parameters": {...}}, ...]
        """
        p = NativeFunctionCallingAdapter.normalize_provider(provider)
        if p in {"openai", "qwen", "deepseek"}:
            return NativeFunctionCallingAdapter._parse_openai_compatible_calls(response)
        return NativeFunctionCallingAdapter._parse_gemini_calls(response)

    @staticmethod
    def _parse_openai_compatible_calls(response: Any) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        choices = getattr(response, "choices", None)
        if not choices:
            return out
        msg = getattr(choices[0], "message", None)
        if not msg:
            return out
        tool_calls = getattr(msg, "tool_calls", None) or []
        for tc in tool_calls:
            fn = getattr(tc, "function", None)
            name = getattr(fn, "name", None)
            raw_args = getattr(fn, "arguments", None)
            params: Dict[str, Any] = {}
            if isinstance(raw_args, str) and raw_args.strip():
                try:
                    parsed = json.loads(raw_args)
                    if isinstance(parsed, dict):
                        params = parsed
                except Exception:
                    params = {}
            out.append({
                "id": getattr(tc, "id", None),
                "name": name,
                "parameters": params,
            })
        return [x for x in out if x.get("name")]

    @staticmethod
    def _parse_gemini_calls(response: Any) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if isinstance(response, dict):
            candidates = response.get("candidates") or []
        else:
            candidates = getattr(response, "candidates", None) or []

        for candidate in candidates:
            content = candidate.get("content") if isinstance(candidate, dict) else getattr(candidate, "content", None)
            if not content:
                continue
            parts = content.get("parts") if isinstance(content, dict) else getattr(content, "parts", None)
            for part in parts or []:
                function_call = part.get("functionCall") if isinstance(part, dict) else getattr(part, "function_call", None)
                if not function_call:
                    continue
                name = function_call.get("name") if isinstance(function_call, dict) else getattr(function_call, "name", None)
                args = function_call.get("args") if isinstance(function_call, dict) else getattr(function_call, "args", None)
                out.append({"id": None, "name": name, "parameters": args if isinstance(args, dict) else {}})
        return [x for x in out if x.get("name")]

    @staticmethod
    def build_tool_result_messages(provider: str, call_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        根据工具调用结果生成给模型的回填消息。
        call_results 元素:
        {"id":..., "name":"tool", "result": Any}
        """
        p = NativeFunctionCallingAdapter.normalize_provider(provider)
        if p in {"openai", "qwen", "deepseek"}:
            messages: List[Dict[str, Any]] = []
            for item in call_results:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": item.get("id"),
                        "name": item.get("name"),
                        "content": json.dumps(item.get("result"), ensure_ascii=False),
                    }
                )
            return messages

        # Gemini: functionResponse part
        parts: List[Dict[str, Any]] = []
        for item in call_results:
            parts.append(
                {
                    "functionResponse": {
                        "name": item.get("name"),
                        "response": {"result": item.get("result")},
                    }
                }
            )
        return [{"role": "user", "parts": parts}] if parts else []
