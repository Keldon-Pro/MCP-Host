import os
import json
import logging
import time
import re
from pathlib import Path
from typing import Dict, Optional, Any, List, Tuple

from mcp_client import MCPClient, MCPClientError, MCPStdioClient
import constants

LOGGER = logging.getLogger(__name__)

class MCPHost:
    """
    模块: mcp_host
    作用: 管理多台 MCP 服务器的启用/禁用与客户端生命周期
    - 读取 `mcp_server_config.json`，构建服务器映射与状态
    - 为每台服务器创建对应类型的客户端（HTTP 或 STDIO）
    - 暴露工具列表与工具调用的统一入口
    """
    def __init__(self, config_path: Optional[str] = None, prewarm: bool = False):
        """
        初始化管理器并加载配置。
        - `config_path`: 配置文件路径；默认项目根目录下 `mcp_server_config.json`
        - `prewarm`: 是否在启动时对 stdio 服务器进行工具列表预热
        """
        # 解析配置路径与内部状态容器
        self.config_path = config_path or constants.DEFAULT_CONFIG_PATH
        self._cfg: Dict[str, Any] = {}
        self._servers: Dict[str, Dict[str, Any]] = {}
        self._clients: Dict[str, MCPClient] = {}
        self.load_config(self.config_path)
        if prewarm:
            self.start(prewarm=True)

    @staticmethod
    def _load_json(path: str) -> Any:
        p = Path(path)
        if not p.exists():
            return None
        try:
            try:
                text = p.read_text(encoding="utf-8-sig")
            except Exception:
                text = p.read_text(encoding="utf-8")
            return json.loads(text)
        except Exception:
            return None

    @staticmethod
    def _save_json(path: str, data: Any) -> bool:
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            return True
        except Exception:
            return False

    def load_config(self, path: Optional[str] = None) -> None:
        """
        读取配置文件并归一化到内部 `self._servers` 映射。
        - 仅支持 `mcpServers{}` 键值结构
        - 为每个服务器条目记录 `name/url/headers/enabled/type/note/status`
        - 文件编码使用 `utf-8`，异常时降级为空配置
        """
        # 读取并解析 JSON 配置，允许两种结构并合并到统一的字典
        self.config_path = path or self.config_path
        self._cfg = self._load_json(self.config_path) or {}
        servers_map = {}
        m = self._cfg.get("mcpServers") or {}
        for name, entry in m.items():
            # 新版配置结构：键值对形式，包含 type 与 note
            servers_map[name] = {
                "name": name,
                "url": entry.get("url"),
                "headers": entry.get("headers") or {},
                "enabled": entry.get("enabled", True),
                "type": entry.get("type"),
                "note": entry.get("note"),
                "status": "unknown",
            }
        self._servers = servers_map

    def start(self, prewarm: bool = True) -> None:
        """
        启动所有启用状态的服务器客户端。
        - `type=stdio` 使用 `MCPStdioClient`，否则使用 `MCPClient`
        - 预热: 对 stdio 类型执行一次 `tools/list` 以加速后续调用
        - 失败时将服务器标记为 `down`
        """
        # 遍历启用的服务器，为其创建对应类型的客户端并可选预热
        for name, meta in self._servers.items():
            if not meta.get("enabled"):
                continue
            if name in self._clients and self._clients[name]:
                continue
            try:
                typ = str(meta.get("type")) if meta.get("type") is not None else "http"
                if typ == "stdio":
                    client = MCPStdioClient(server_name=name, config_path=self.config_path)
                else:
                    client = MCPClient(server_name=name, config_path=self.config_path)
                self._clients[name] = client
                self._servers[name]["status"] = "running"
                if prewarm:
                    try:
                        if typ == "stdio":
                            # 通过工具列表调用提前“唤醒”子进程服务，减少首次调用开销
                            LOGGER.info("[Prewarm] stdio server '%s' listing tools...", name)
                            t0 = time.perf_counter()
                            res = client.list_tools()
                            dt = int((time.perf_counter() - t0) * 1000)
                            cnt = len((res or {}).get("tools") or [])
                            LOGGER.info("[Prewarm] stdio server '%s' ready in %dms, tools=%d", name, dt, cnt)
                        else:
                            LOGGER.info("[Prewarm] skip http server '%s'", name)
                    except Exception:
                        LOGGER.warning("[Prewarm] server '%s' prewarm failed", name)
            except MCPClientError:
                self._servers[name]["status"] = "down"

    def enable_server(self, name: str) -> bool:
        """
        启用指定服务器并确保客户端已创建。
        返回: `True` 表示成功创建或已存在；失败时标记为 `down`。
        """
        # 将服务器标记为启用，并懒创建其客户端（HTTP 或 STDIO）
        meta = self._servers.get(name)
        if not meta:
            return False
        self._servers[name]["enabled"] = True
        if name not in self._clients or not self._clients[name]:
            try:
                typ = str(meta.get("type")) if meta.get("type") is not None else "http"
                if typ == "stdio":
                    self._clients[name] = MCPStdioClient(server_name=name, config_path=self.config_path)
                else:
                    self._clients[name] = MCPClient(server_name=name, config_path=self.config_path)
                self._servers[name]["status"] = "running"
            except MCPClientError:
                self._servers[name]["status"] = "down"
                return False
        return True

    def disable_server(self, name: str) -> bool:
        """
        禁用指定服务器并移除其客户端。
        - 安全删除客户端字典项并更新状态为 `disabled`
        返回: `True` 表示操作完成或服务器不存在客户端也视为成功。
        """
        # 更新启用状态并从缓存中移除客户端实例
        meta = self._servers.get(name)
        if not meta:
            return False
        self._servers[name]["enabled"] = False
        if name in self._clients:
            try:
                del self._clients[name]
            except Exception:
                pass
        self._servers[name]["status"] = "disabled"
        return True

    def list_servers(self) -> List[Dict[str, Any]]:
        """
        返回当前已知服务器的简要状态列表。
        字段: `name/enabled/status`。
        """
        # 仅返回启用中的服务器（过滤掉 disabled 项）
        out = []
        for name, meta in self._servers.items():
            if not bool(meta.get("enabled")):
                continue
            out.append({
                "name": name,
                "enabled": True,
                "status": meta.get("status") or "unknown",
            })
        return out

    def list_tools(self, name: str) -> Dict[str, Any]:
        """
        拉取指定服务器的工具列表。
        - 若服务器未启用或客户端不可用，尝试启用并创建客户端
        返回: `{"tools": [...], "remote_enabled": bool}`；失败时空列表与 `remote_enabled=False`。
        """
        # 确保服务器启用且客户端可用后，调用工具列表接口
        if not self._servers.get(name) or not self._servers[name].get("enabled"):
            return {"tools": [], "remote_enabled": False}
        client = self._clients.get(name)
        if not client:
            ok = self.enable_server(name)
            if not ok:
                return {"tools": [], "remote_enabled": False}
            client = self._clients.get(name)
        try:
            return client.list_tools()
        except Exception:
            return {"tools": [], "remote_enabled": False}

    def load_states(self) -> Dict[str, Any]:
        # 读取工具状态文件（包含每个服务器工具的启用开关与备注）
        p = constants.TOOL_STATES_PATH
        d = self._load_json(p)
        if d is None:
            # 初始化空文件
            self._save_json(p, {})
            return {}
        return d if isinstance(d, dict) else {}

    def list_all_tools(self) -> Dict[str, Dict[str, Any]]:
        # 聚合所有启用服务器的工具，并结合状态文件过滤掉关闭的工具
        reg: Dict[str, Dict[str, Any]] = {}
        states = self.load_states()
        for s in self.list_servers():
            name = s.get("name")
            if not name or not s.get("enabled"):
                continue
            
            # Check if server is disabled in tool_states.json
            sstate = states.get(name) or {}
            if sstate.get("enabled") is False:
                continue

            info = self.list_tools(name) or {"tools": []}
            tools = info.get("tools") or []
            tstate = (sstate.get("tools") if isinstance(sstate.get("tools"), dict) else {}) or {}
            for t in tools:
                n = t.get("name")
                if not n:
                    continue
                if n in tstate:
                    v = tstate.get(n) or {}
                    try:
                        if not bool(v.get("turn-on")):
                            # 工具被显式关闭，则不纳入注册表
                            continue
                    except Exception:
                        pass
                reg[n] = {"server": name, "schema": t}
        return reg

    def extract_param_schema(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        cand = schema.get("inputSchema") if isinstance(schema, dict) else None
        return cand if isinstance(cand, dict) else {}

    def tools_guide(self, registry: Dict[str, Dict[str, Any]]) -> str:
        # 基于工具的描述、JSON Schema 与状态备注，生成可读的参数指南
        lines: List[str] = []
        states = self.load_states()
        
        # Sort by server order in tool_states.json, then by tool name
        server_order = list(states.keys())
        
        def sort_key(tool_name):
            server = registry[tool_name]["server"]
            try:
                idx = server_order.index(server)
            except ValueError:
                idx = 999999
            return (idx, server, tool_name)
            
        sorted_keys = sorted(registry.keys(), key=sort_key)
        
        for tool_name in sorted_keys:
            schema = registry[tool_name]["schema"]
            server = registry[tool_name]["server"]
            desc = (schema.get("description") or schema.get("summary") or schema.get("note") or "") if isinstance(schema, dict) else ""
            
            lines.append(f"[Tool] {tool_name}")
            if desc:
                lines.append(f"  Description: {desc}")
            
            try:
                note = ""
                sstate = states.get(server) or {}
                tstate = (sstate.get("tools") if isinstance(sstate.get("tools"), dict) else {}) or {}
                entry = tstate.get(tool_name) or {}
                if isinstance(entry.get("note"), str):
                    note = (entry.get("note") or "").strip()
                if note:
                    # 若工具在状态文件中带有备注，则插入到指南中
                    lines.append(f"  Note: {note}")
            except Exception:
                pass
                
            ps = self.extract_param_schema(schema)
            props = (ps.get("properties") if isinstance(ps, dict) else None) or {}
            required = (ps.get("required") if isinstance(ps, dict) else None) or []
            
            if props:
                lines.append("  Parameters:")
                for k, v in props.items():
                    typ = v.get("type") if isinstance(v, dict) else None
                    dsc = v.get("description") if isinstance(v, dict) else None
                    req = "required" if k in required else "optional"
                    seg = f"    - {k} ({typ or 'any'}, {req})"
                    if dsc:
                        seg += f": {dsc}"
                    lines.append(seg)
            else:
                alt = schema.get("parameters") if isinstance(schema, dict) else None
                if alt is None:
                    alt = schema.get("args") if isinstance(schema, dict) else None
                if isinstance(alt, list) and alt:
                    lines.append("  Parameters:")
                    for p in alt:
                        name = p.get("name") or "param"
                        typ = p.get("type") or "any"
                        req = "required" if p.get("required") else "optional"
                        dsc = p.get("description") or None
                        seg = f"    - {name} ({typ}, {req})"
                        if dsc:
                            seg += f": {dsc}"
                        lines.append(seg)
                else:
                    # 无法从 schema/parameters/args 推断参数细节
                    lines.append("  Parameters: (No detailed information available)")
            lines.append("-" * 50)
        return "\n".join(lines)

    def detect_tool(self, text: str) -> Tuple[bool, Dict[str, Any]]:
        if not isinstance(text, str):
            return False, {}
        m = re.search(r"<tool>\s*(\{[\s\S]*?\})\s*</tool>", text, re.IGNORECASE)
        if not m:
            return False, {}
        try:
            spec = json.loads(m.group(1))
        except Exception:
            spec = {}
        return bool(spec), spec

    def _registry_to_function_schemas(self, registry: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        """
        将工具注册表统一转换为函数 schema 列表：
        每项包含 `name/description/parameters/server`。
        """
        reg = registry or self.list_all_tools()
        items: List[Dict[str, Any]] = []
        for tool_name, info in (reg or {}).items():
            schema = (info or {}).get("schema") or {}
            server = (info or {}).get("server")
            desc = ""
            if isinstance(schema, dict):
                desc = (schema.get("description") or schema.get("summary") or schema.get("note") or "")
            params = self.extract_param_schema(schema)
            if not isinstance(params, dict) or not params:
                params = {"type": "object", "properties": {}}
            items.append({
                "name": tool_name,
                "description": desc or "",
                "parameters": params,
                "server": server,
            })
        return items

    def tools_for_openai(self, registry: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        """
        生成 OpenAI function calling 的 `tools` 参数。
        参考格式：`[{type:"function", function:{name,description,parameters}}]`
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": it["name"],
                    "description": it["description"],
                    "parameters": it["parameters"],
                },
            }
            for it in self._registry_to_function_schemas(registry)
        ]

    def tools_for_qwen(self, registry: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        """
        生成 Qwen function calling 的 `tools` 参数。
        Qwen 与 OpenAI 兼容，采用相同结构。
        """
        return self.tools_for_openai(registry)

    def tools_for_deepseek(self, registry: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        """
        生成 DeepSeek function calling 的 `tools` 参数。
        DeepSeek 与 OpenAI 兼容，采用相同结构。
        """
        return self.tools_for_openai(registry)

    def tools_for_gemini(self, registry: Optional[Dict[str, Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        """
        生成 Gemini function calling 的 `tools` 参数（function_declarations 风格）。
        可直接用于：
        `tools=[{"function_declarations": ...}]`
        """
        declarations: List[Dict[str, Any]] = []
        for it in self._registry_to_function_schemas(registry):
            declarations.append({
                "name": it["name"],
                "description": it["description"],
                "parameters": it["parameters"],
            })
        return [{"function_declarations": declarations}]

    def detect_native_tool_calls(self, provider: str, message: Any) -> List[Dict[str, Any]]:
        """
        从不同模型供应商的原生 function calling 响应中提取工具调用，统一为：
        `{type:"function", name, parameters, id?}` 列表。
        - provider: openai / qwen / deepseek / gemini
        - message: SDK message 对象或字典
        """
        p = (provider or "").strip().lower()
        out: List[Dict[str, Any]] = []
        if message is None:
            return out

        # --- OpenAI / Qwen / DeepSeek (tool_calls) ---
        if p in {"openai", "qwen", "deepseek"}:
            tool_calls = None
            if isinstance(message, dict):
                tool_calls = message.get("tool_calls")
            else:
                tool_calls = getattr(message, "tool_calls", None)
            if not tool_calls:
                return out
            for tc in tool_calls:
                if isinstance(tc, dict):
                    fn = tc.get("function") or {}
                    cid = tc.get("id")
                else:
                    fn = getattr(tc, "function", None)
                    cid = getattr(tc, "id", None)
                if not fn:
                    continue
                if isinstance(fn, dict):
                    name = fn.get("name")
                    raw_args = fn.get("arguments")
                else:
                    name = getattr(fn, "name", None)
                    raw_args = getattr(fn, "arguments", None)
                if not name:
                    continue
                params: Dict[str, Any] = {}
                if isinstance(raw_args, dict):
                    params = raw_args
                elif isinstance(raw_args, str):
                    try:
                        parsed = json.loads(raw_args)
                        if isinstance(parsed, dict):
                            params = parsed
                    except Exception:
                        params = {}
                spec = {"type": "function", "name": name, "parameters": params}
                if cid:
                    spec["id"] = cid
                out.append(spec)
            return out

        # --- Gemini (functionCall) ---
        if p == "gemini":
            parts = None
            if isinstance(message, dict):
                parts = ((message.get("content") or {}).get("parts") or message.get("parts"))
            else:
                content = getattr(message, "content", None)
                parts = getattr(content, "parts", None) if content is not None else getattr(message, "parts", None)
            if not isinstance(parts, list):
                return out
            for part in parts:
                fc = None
                if isinstance(part, dict):
                    fc = part.get("functionCall") or part.get("function_call")
                else:
                    fc = getattr(part, "function_call", None) or getattr(part, "functionCall", None)
                if not fc:
                    continue
                if isinstance(fc, dict):
                    name = fc.get("name")
                    args = fc.get("args") or {}
                else:
                    name = getattr(fc, "name", None)
                    args = getattr(fc, "args", None) or {}
                if not name:
                    continue
                out.append({"type": "function", "name": name, "parameters": (args if isinstance(args, dict) else {})})
            return out
        return out

    def call_tool(self, spec: Dict[str, Any], formated: bool = True) -> str:
        # 接受 `<tool>` JSON 契约，按注册表定位服务器并执行调用
        name = (spec or {}).get("name")
        params = (spec or {}).get("parameters") or {}
        server = (spec or {}).get("server")
        if not name:
            return json.dumps({"error": "缺少工具名", "spec": spec}, ensure_ascii=False, indent=2)
        if not server:
            registry = self.list_all_tools()
            if name not in registry:
                return json.dumps({"error": "未找到匹配的工具", "spec": spec}, ensure_ascii=False, indent=2)
            server = registry[name]["server"]
        try:
            res_str = self.call_server_tool(server, name, **params)
            try:
                res_obj = json.loads(res_str)
            except Exception:
                res_obj = res_str
            payload = {"name": name, "server": server, "result": res_obj}
            if formated:
                return json.dumps(payload, ensure_ascii=False, indent=2)
            else:
                return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except Exception as e:
            return json.dumps({"name": name, "server": server, "error": str(e)}, ensure_ascii=False, indent=2)

    def call_native_tool_calls(self, tool_calls: List[Dict[str, Any]], formated: bool = True) -> List[Dict[str, Any]]:
        """
        批量执行原生 function calling 提取出的调用项。
        输入: `[{type:"function", name, parameters, id?}, ...]`
        返回: `[{id?, name, result_json}, ...]`
        """
        out: List[Dict[str, Any]] = []
        for spec in tool_calls or []:
            one = self.call_tool(spec, formated=formated)
            item = {
                "name": (spec or {}).get("name"),
                "result_json": one,
            }
            if (spec or {}).get("id"):
                item["id"] = spec.get("id")
            out.append(item)
        return out

    def call_server_tool(self, name: str, tool: str, **params) -> str:
        """
        在指定服务器上调用某个工具。
        - 自动确保客户端可用；失败返回 `None`
        返回: 工具返回的字典或 `None`
        """
        # 确保服务器启用并懒创建客户端，然后执行调用
        if not self._servers.get(name) or not self._servers[name].get("enabled"):
            return "null"
        client = self._clients.get(name)
        if not client:
            ok = self.enable_server(name)
            if not ok:
                return "null"
            client = self._clients.get(name)
        try:
            res = client.call_tool(tool, **params)
            return json.dumps(res, ensure_ascii=False, separators=(",", ":")) if res is not None else "null"
        except Exception:
            return "null"

    def health_check(self, name: Optional[str] = None) -> Dict[str, Any]:
        """
        对指定或全部服务器执行健康检查。
        - 启用状态下调用客户端 `ping()` 判断运行/宕机
        返回: `name -> { enabled, status }` 映射。
        """
        # 若客户端缺失会进行一次启用尝试；结果写回到服务器状态
        result = {}
        targets = [name] if name else list(self._servers.keys())
        for n in targets:
            meta = self._servers.get(n)
            if not meta:
                result[n] = {"enabled": False, "status": "missing"}
                continue
            if not meta.get("enabled"):
                result[n] = {"enabled": False, "status": "disabled"}
                continue
            client = self._clients.get(n)
            if not client:
                ok = self.enable_server(n)
                if not ok:
                    result[n] = {"enabled": True, "status": "down"}
                    continue
                client = self._clients.get(n)
            try:
                ok = client.ping()
                self._servers[n]["status"] = "running" if ok else "down"
                result[n] = {"enabled": True, "status": self._servers[n]["status"]}
            except Exception:
                self._servers[n]["status"] = "down"
                result[n] = {"enabled": True, "status": "down"}
        return result

    def reload_config(self, path: Optional[str] = None) -> None:
        """
        重新加载配置并同步服务器/客户端状态。
        - 移除已禁用或缺失的客户端，将其状态设为 `disabled`
        - 对启用但无客户端的条目重新创建客户端
        """
        # 重新读取配置，并使客户端缓存与启用状态保持一致
        self.load_config(path or self.config_path)
        for name, meta in list(self._clients.items()):
            if not self._servers.get(name) or not self._servers[name].get("enabled"):
                try:
                    del self._clients[name]
                except Exception:
                    pass
                if name in self._servers:
                    self._servers[name]["status"] = "disabled"
        for name in self._servers.keys():
            if self._servers[name].get("enabled") and name not in self._clients:
                try:
                    typ = str(self._servers[name].get("type")) if self._servers[name].get("type") is not None else "http"
                    if typ == "stdio":
                        self._clients[name] = MCPStdioClient(server_name=name, config_path=self.config_path)
                    else:
                        self._clients[name] = MCPClient(server_name=name, config_path=self.config_path)
                    self._servers[name]["status"] = "running"
                except MCPClientError:
                    self._servers[name]["status"] = "down"

    def get_client(self, name: str) -> Optional[MCPClient]:
        """
        获取指定服务器的客户端实例（可能为 HTTP 或 STDIO）。
        返回: 客户端或 `None`。
        """
        # 用于直接访问底层客户端能力（例如调试或扩展）
        return self._clients.get(name)

    def list_prompts(self, name: str) -> Dict[str, Any]:
        """
        拉取指定服务器的 Prompt 列表。
        - 自动处理客户端连接
        """
        if not self._servers.get(name) or not self._servers[name].get("enabled"):
            return {"prompts": [], "remote_enabled": False}
        client = self._clients.get(name)
        if not client:
            if not self.enable_server(name):
                return {"prompts": [], "remote_enabled": False}
            client = self._clients.get(name)
        try:
            return client.list_prompts()
        except Exception:
            return {"prompts": [], "remote_enabled": False}

    def list_resources(self, name: str) -> Dict[str, Any]:
        """
        拉取指定服务器的 Resource 列表。
        - 自动处理客户端连接
        """
        if not self._servers.get(name) or not self._servers[name].get("enabled"):
            return {"resources": [], "remote_enabled": False}
        client = self._clients.get(name)
        if not client:
            if not self.enable_server(name):
                return {"resources": [], "remote_enabled": False}
            client = self._clients.get(name)
        try:
            return client.list_resources()
        except Exception:
            return {"resources": [], "remote_enabled": False}

    def get_server_config(self) -> Dict[str, Any]:
        """返回当前的完整配置（原始字典）"""
        return self._cfg

    def save_server_config(self, cfg: Dict[str, Any]) -> bool:
        """保存配置到文件，并触发重载"""
        ok = self._save_json(self.config_path, cfg)
        if ok:
            self.reload_config()
        return ok

    def get_server_order(self) -> List[str]:
        p = constants.SERVER_ORDER_PATH
        d = self._load_json(p)
        return d if isinstance(d, list) else []

    def save_server_order(self, order: List[str]) -> bool:
        p = constants.SERVER_ORDER_PATH
        return self._save_json(p, list(order))

    def save_states(self, states: Dict[str, Any]) -> bool:
        p = constants.TOOL_STATES_PATH
        return self._save_json(p, states)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")
    host = MCPHost(prewarm=False)
    print(json.dumps({"servers": host.list_servers()}, ensure_ascii=False, indent=2))
    for s in host.list_servers():
        if s.get("enabled"):
            tools = host.list_tools(s["name"])
            print(json.dumps({"server": s["name"], "tools_count": len(tools.get("tools") or [])}, ensure_ascii=False, indent=2))
