import os
import json
import logging
import time
import re
from pathlib import Path
from typing import Dict, Optional, Any, List, Tuple

from mcp_client import MCPClient, MCPClientError, MCPStdioClient

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
        self.config_path = config_path or str(Path(__file__).resolve().parent / "config" / "mcp_server_config.json")
        self._cfg: Dict[str, Any] = {}
        self._servers: Dict[str, Dict[str, Any]] = {}
        self._clients: Dict[str, MCPClient] = {}
        self.load_config(self.config_path)
        if prewarm:
            self.start(prewarm=True)

    def load_config(self, path: Optional[str] = None) -> None:
        """
        读取配置文件并归一化到内部 `self._servers` 映射。
        - 仅支持 `mcpServers{}` 键值结构
        - 为每个服务器条目记录 `name/url/headers/enabled/type/note/status`
        - 文件编码使用 `utf-8`，异常时降级为空配置
        """
        # 读取并解析 JSON 配置，允许两种结构并合并到统一的字典
        p = Path(path or self.config_path)
        if p.exists():
            try:
                self._cfg = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                self._cfg = {}
        else:
            self._cfg = {}
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
        p = str(Path(__file__).resolve().parent / "config" / "tool_states.json")
        f = Path(p)
        if not f.exists():
            try:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text("{}", encoding="utf-8")
            except Exception:
                pass
            return {}
        try:
            try:
                t = f.read_text(encoding="utf-8-sig")
            except Exception:
                t = f.read_text(encoding="utf-8")
            d = json.loads(t)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def list_all_tools(self) -> Dict[str, Dict[str, Any]]:
        # 聚合所有启用服务器的工具，并结合状态文件过滤掉关闭的工具
        reg: Dict[str, Dict[str, Any]] = {}
        states = self.load_states()
        for s in self.list_servers():
            name = s.get("name")
            if not name or not s.get("enabled"):
                continue
            info = self.list_tools(name) or {"tools": []}
            tools = info.get("tools") or []
            sstate = states.get(name) or {}
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
        for tool_name in sorted(registry.keys()):
            schema = registry[tool_name]["schema"]
            server = registry[tool_name]["server"]
            desc = (schema.get("description") or schema.get("summary") or schema.get("note") or "") if isinstance(schema, dict) else ""
            if desc:
                lines.append(f"- {tool_name}: {desc}")
            else:
                lines.append(f"- {tool_name}:")
            try:
                note = ""
                sstate = states.get(server) or {}
                tstate = (sstate.get("tools") if isinstance(sstate.get("tools"), dict) else {}) or {}
                entry = tstate.get(tool_name) or {}
                if isinstance(entry.get("note"), str):
                    note = (entry.get("note") or "").strip()
                if note:
                    # 若工具在状态文件中带有备注，则插入到指南中
                    lines.append(f"  note: {note}")
            except Exception:
                pass
            ps = self.extract_param_schema(schema)
            props = (ps.get("properties") if isinstance(ps, dict) else None) or {}
            required = (ps.get("required") if isinstance(ps, dict) else None) or []
            if props:
                for k, v in props.items():
                    typ = v.get("type") if isinstance(v, dict) else None
                    dsc = v.get("description") if isinstance(v, dict) else None
                    req = "required" if k in required else "optional"
                    seg = f"  {k} ({typ or 'any'}, {req})"
                    if dsc:
                        seg += f": {dsc}"
                    lines.append(seg)
            else:
                alt = schema.get("parameters") if isinstance(schema, dict) else None
                if alt is None:
                    alt = schema.get("args") if isinstance(schema, dict) else None
                if isinstance(alt, list) and alt:
                    for p in alt:
                        name = p.get("name") or "param"
                        typ = p.get("type") or "any"
                        req = "required" if p.get("required") else "optional"
                        dsc = p.get("description") or None
                        seg = f"  {name} ({typ}, {req})"
                        if dsc:
                            seg += f": {dsc}"
                        lines.append(seg)
                else:
                    # 无法从 schema/parameters/args 推断参数细节
                    lines.append("  (参数信息不可用)")
            lines.append("")
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

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")
    host = MCPHost(prewarm=False)
    print(json.dumps({"servers": host.list_servers()}, ensure_ascii=False, indent=2))
    for s in host.list_servers():
        if s.get("enabled"):
            tools = host.list_tools(s["name"])
            print(json.dumps({"server": s["name"], "tools_count": len(tools.get("tools") or [])}, ensure_ascii=False, indent=2))
