from __future__ import annotations
import os
import json
import logging
import uuid
from typing import Any, Dict, Optional
from pathlib import Path

import requests
import subprocess
import threading
import queue
import shutil
try:
    from dotenv import load_dotenv
    load_dotenv(override=False)
except Exception:
    pass

LOGGER = logging.getLogger(__name__)


class MCPClientError(Exception):
    pass

"""
模块: mcp_client
作用: 提供两种 MCP 客户端实现
- `MCPClient`: 通过 HTTP(JSON-RPC) 与远程 MCP 服务器交互
- `MCPStdioClient`: 通过子进程标准输入/输出与本地 MCP 服务器交互
特点:
- 支持从 `mcp_server_config.json` 与环境变量读取服务器配置
- 封装工具/提示/资源的列表与调用接口
"""
class MCPClient:
    """
    简介:
    - 通用 MCP 客户端，按远程工具名进行调用；仅执行远程 MCP 请求，不做本地 REST 回退
    - 支持通过 `mcp_server_config.json` 或环境变量选择不同 MCP Server，并可附加自定义请求头

    配置:
    - 配置文件: `MCP_SERVER_CONFIG_PATH` 指向 JSON，仅支持 `mcpServers{}` 键值结构；`MCP_SERVER_NAME` 指定目标服务器
    - 环境变量: `MCP_CONNECTION_URL` 或 `MCP_SERVER_URL` 提供连接地址；必要时可传入密钥/鉴权参数

    接口:
    - `list_tools()`: 拉取工具目录
    - `call_tool(name, **params)`: 执行工具调用
    - `ping()`: 简单健康检查示例
    """
    MCP_BASE_URL = os.getenv("MCP_CONNECTION_URL") or os.getenv("MCP_SERVER_URL", "")

    def __init__(self, api_key: Optional[str] = None, timeout: float = 15.0, enable_remote: bool = True, server_name: Optional[str] = None, config_path: Optional[str] = None, headers: Optional[Dict[str, str]] = None):
        """
        初始化客户端:
        - `api_key`: 高德 API 密钥；若为空则从环境变量读取
        - `timeout`: 网络请求超时时间(秒)
        - `enable_remote`: 是否启用远程 MCP 调用优先
        """
        self.api_key = api_key or os.getenv("AMAP_API_KEY") or os.getenv("AMAP_MAPS_API_KEY")
        self.timeout = timeout
        self.enable_remote = enable_remote
        self._headers = headers or {"Accept": "application/json, text/event-stream"}
        cfg_path = config_path or str(Path(__file__).resolve().parent / "config" / "mcp_server_config.json")
        chosen = server_name or os.getenv("MCP_SERVER_NAME")
        cfg_url, cfg_headers = self._resolve_server_config(cfg_path, chosen)
        if cfg_url:
            self.MCP_BASE_URL = cfg_url
        if cfg_headers:
            self._headers.update(cfg_headers)
        if not self.MCP_BASE_URL:
            raise MCPClientError("No MCP server URL configured. Set MCP_CONNECTION_URL or provide mcp_server_config.json.")

    def _remote_url(self) -> str:
        return self.MCP_BASE_URL

    def _resolve_server_config(self, cfg_path: str, chosen: Optional[str]) -> (Optional[str], Optional[Dict[str, str]]):
        """
        从配置文件解析目标服务器的 `url` 与附加请求头。
        - 仅支持 `mcpServers: { name: { url, headers } }` 键值结构
        - 文件编码兼容 `utf-8-sig`（BOM）与 `utf-8`。
        返回: `(url, headers)`，任一无法解析时返回 `None`。
        """
        try:
            p = Path(cfg_path)
            if not p.exists():
                return None, None
            try:
                text = p.read_text(encoding="utf-8-sig")
            except Exception:
                text = p.read_text(encoding="utf-8")
            data = json.loads(text)
            m = data.get("mcpServers") or {}
            if m:
                name = chosen
                keys = list(m.keys())
                sel = name if (name and name in m) else (keys[0] if keys else None)
                if not sel:
                    return None, None
                entry = m.get(sel) or {}
                url = (entry.get("url") or "").strip()
                hdrs = entry.get("headers") or {}
                return url, hdrs
        except Exception:
            return None, None

    def _call(self, remote_tool: str, rest_func, **params) -> Optional[Dict[str, Any]]:
        """
        通用调用入口(仅远程):
        - 直接使用远程 MCP 工具名进行调用；失败时记录错误并返回 None。
        - `rest_func` 参数不再使用。
        返回: dict 或 None。
        """
        if not self.enable_remote:
            return None
        req_id = str(uuid.uuid4())
        payload = {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": remote_tool, "arguments": params}, "id": req_id}
        try:
            resp = requests.post(self._remote_url(), json=payload, timeout=self.timeout, stream=False, headers=self._headers)
            if resp.status_code != 200:
                LOGGER.debug("Remote MCP call non-200 (%s) -> fallback", resp.status_code)
                remote_raw = None
            else:
                try:
                    data = resp.json()
                    remote_raw = data["result"] if isinstance(data, dict) and "result" in data else data
                except Exception:
                    LOGGER.debug("Remote MCP response not JSON decodable -> fallback")
                    remote_raw = None
        except requests.RequestException as e:
            LOGGER.debug("Remote MCP request failed: %s", e)
            remote_raw = None
        if not remote_raw:
            LOGGER.error("MCP call failed for %s", remote_tool)
            return None
        result_candidate = remote_raw.get("data") if isinstance(remote_raw, dict) else None
        result = result_candidate or remote_raw
        return result

    def list_tools(self) -> Dict[str, Any]:
        """
        拉取远程 MCP 服务器的工具目录。
        返回: `{"tools": [...], "remote_enabled": bool}`；失败时 `tools` 为空列表。
        """
        payload = {"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": str(uuid.uuid4())}
        try:
            resp = requests.post(self._remote_url(), json=payload, timeout=self.timeout, stream=False, headers=self._headers)
            if resp.status_code != 200:
                return {"tools": [], "remote_enabled": self.enable_remote}
            try:
                data = resp.json()
            except Exception:
                return {"tools": [], "remote_enabled": self.enable_remote}
            return {"tools": (data.get("result", {}).get("tools") if isinstance(data, dict) else []), "remote_enabled": self.enable_remote}
        except requests.RequestException:
            return {"tools": [], "remote_enabled": self.enable_remote}

    def list_prompts(self) -> Dict[str, Any]:
        """
        拉取远程 MCP 服务器的提示词目录。
        返回: `{"prompts": [...], "remote_enabled": bool}`；失败时 `prompts` 为空列表。
        """
        payload = {"jsonrpc": "2.0", "method": "prompts/list", "params": {}, "id": str(uuid.uuid4())}
        try:
            resp = requests.post(self._remote_url(), json=payload, timeout=self.timeout, stream=False, headers=self._headers)
            if resp.status_code != 200:
                return {"prompts": [], "remote_enabled": self.enable_remote}
            try:
                data = resp.json()
            except Exception:
                return {"prompts": [], "remote_enabled": self.enable_remote}
            return {"prompts": (data.get("result", {}).get("prompts") if isinstance(data, dict) else []), "remote_enabled": self.enable_remote}
        except requests.RequestException:
            return {"prompts": [], "remote_enabled": self.enable_remote}

    def list_resources(self) -> Dict[str, Any]:
        """
        拉取远程 MCP 服务器的资源目录。
        返回: `{"resources": [...], "remote_enabled": bool}`；失败时 `resources` 为空列表。
        """
        payload = {"jsonrpc": "2.0", "method": "resources/list", "params": {}, "id": str(uuid.uuid4())}
        try:
            resp = requests.post(self._remote_url(), json=payload, timeout=self.timeout, stream=False, headers=self._headers)
            if resp.status_code != 200:
                return {"resources": [], "remote_enabled": self.enable_remote}
            try:
                data = resp.json()
            except Exception:
                return {"resources": [], "remote_enabled": self.enable_remote}
            return {"resources": (data.get("result", {}).get("resources") if isinstance(data, dict) else []), "remote_enabled": self.enable_remote}
        except requests.RequestException:
            return {"resources": [], "remote_enabled": self.enable_remote}

    def call_tool(self, name: str, **params) -> Optional[Dict[str, Any]]:
        """
        执行指定工具调用（HTTP 模式）。
        - `name`: 工具名
        - `params`: 工具参数（字典）
        返回: 远程返回的 `result.data` 或原始结果；失败返回 `None`。
        """
        return self._call(name, None, **params)


    def ping(self) -> bool:
        """
        简易健康检查：调用示例工具 `maps_weather`。
        返回: 远程调用有返回值为 `True`，否则 `False`。
        """
        try:
            result = self.call_tool("maps_weather", city="海口")
            return bool(result)
        except Exception:
            return False

    def call_tool_stream(self, name: str, **params):
        """
        以事件流(SSE)方式调用工具并返回迭代器。
        - 将 `Accept` 头设置为 `text/event-stream` 并启用 `stream=True`
        返回: 一个生成器，逐行产出服务器事件；失败返回 `None`。
        """
        hdrs = dict(self._headers)
        hdrs["Accept"] = "text/event-stream"
        payload = {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": name, "arguments": params}, "id": str(uuid.uuid4())}
        try:
            resp = requests.post(self._remote_url(), json=payload, timeout=self.timeout, stream=True, headers=hdrs)
        except requests.RequestException:
            return None
        if resp.status_code != 200:
            return None
        def _iter():
            for line in resp.iter_lines(decode_unicode=True):
                yield line or ""
        return _iter()

class MCPStdioClient:
    """
    通过标准输入/输出与本地 MCP 服务器通信的客户端。
    - 启动子进程，按 JSON-RPC 协议写入请求并读取响应
    - 遵循 MCP 规定的 `initialize` 握手
    - 提供工具/提示/资源的列表与调用接口
    """
    def __init__(self, server_name: Optional[str] = None, config_path: Optional[str] = None, timeout: float = 15.0):
        """
        构造并初始化 stdio 客户端：
        - 从配置中选择 `type=stdio` 的服务器条目，启动子进程
        - 发送 `initialize` 请求进行握手
        参数:
        - `server_name`: 指定服务器名；为空时自动选择首个 stdio 服务器
        - `config_path`: 配置文件路径；默认项目根目录下 `mcp_server_config.json`
        - `timeout`: 请求与读取超时时间（秒）
        """
        self.timeout = timeout
        self._server_name = server_name or os.getenv("MCP_SERVER_NAME")
        cfg_path = config_path or str(Path(__file__).resolve().parent / "config" / "mcp_server_config.json")
        chosen = self._server_name
        entry = self._select_entry(cfg_path, chosen)
        if not entry:
            raise MCPClientError("No stdio server entry found")
        self._proc = None
        self._out_q = queue.Queue()
        self._start(entry)
        init_id = str(uuid.uuid4())
        payload = {
            "jsonrpc": "2.0",
            "id": init_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "MCP_Agent", "version": "0.1.0"},
            },
        }
        resp = self._request(payload)
        if not resp or ("result" not in resp and "error" not in resp):
            raise MCPClientError("Initialize failed")

    def _select_entry(self, cfg_path: str, chosen: Optional[str]) -> Optional[Dict[str, Any]]:
        """
        从配置文件中选择一个 `type=stdio` 的服务器条目。
        优先匹配 `chosen` 名称；未找到则选取首个符合条件的条目。
        仅支持 `mcpServers{}` 键值结构。
        """
        try:
            p = Path(cfg_path)
            if not p.exists():
                return None
            try:
                text = p.read_text(encoding="utf-8-sig")
            except Exception:
                text = p.read_text(encoding="utf-8")
            data = json.loads(text)
            m = data.get("mcpServers") or {}
            if m:
                if chosen and chosen in m:
                    e = m.get(chosen) or {}
                    if str(e.get("type")) == "stdio":
                        return e
                for k in m.keys():
                    e = m.get(k) or {}
                    if str(e.get("type")) == "stdio":
                        return e
            return None
        except Exception:
            return None

    def _start(self, entry: Dict[str, Any]) -> None:
        """
        按条目启动子进程并开始异步读取标准输出。
        - 可设置附加环境变量与工作目录
        - 若命令不可用，抛出明确错误提示 `PATH` 信息
        """
        cmd = entry.get("command") or entry.get("cmd")
        args = entry.get("args") or []
        if not cmd:
            raise MCPClientError("Missing stdio server command")
        env = os.environ.copy()
        add_env = entry.get("env") or {}
        for k, v in add_env.items():
            env[str(k)] = str(v)
        cwd = entry.get("cwd") or None
        exe = shutil.which(cmd) or cmd
        if cwd:
            p = Path(cwd)
            if not p.exists() or not p.is_dir():
                raise MCPClientError(f"Invalid working directory: {cwd}")
        try:
            proc = subprocess.Popen(
                [exe] + list(args),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except FileNotFoundError:
            path_env = env.get("PATH") or ""
            raise MCPClientError(f"Command not found: {cmd}. Ensure it is installed and in PATH. PATH={path_env}")
        self._proc = proc
        t = threading.Thread(target=self._reader, daemon=True)
        t.start()

    def _reader(self) -> None:
        """
        后台线程：逐行读取子进程标准输出并放入队列。
        - 去除行尾换行符；忽略空行
        """
        if not self._proc or not self._proc.stdout:
            return
        for line in self._proc.stdout:
            if not line:
                continue
            s = line.rstrip("\r\n")
            if s:
                self._out_q.put(s)

    def _send(self, obj: Dict[str, Any]) -> None:
        """
        将 JSON 对象序列化为一行并写入子进程标准输入。
        - 使用紧凑分隔符减少体积
        """
        if not self._proc or not self._proc.stdin:
            raise MCPClientError("Process not started")
        data = json.dumps(obj, separators=(",", ":"))
        self._proc.stdin.write(data + "\n")
        self._proc.stdin.flush()

    def _request(self, obj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        发送请求并阻塞等待对应 `id` 的响应。
        - 利用队列接收后台线程推送的标准输出
        - 超时或解析失败返回 `None`
        """
        rid = obj.get("id")
        self._send(obj)
        end = None
        if self.timeout:
            end = (self.timeout)
        while True:
            try:
                s = self._out_q.get(timeout=self.timeout)
            except Exception:
                return None
            try:
                msg = json.loads(s)
            except Exception:
                continue
            if isinstance(msg, dict) and msg.get("id") == rid:
                return msg

    def list_tools(self) -> Dict[str, Any]:
        """
        拉取子进程 MCP 服务器的工具目录。
        返回: `{"tools": [...], "remote_enabled": True}`
        """
        rid = str(uuid.uuid4())
        payload = {"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": rid}
        resp = self._request(payload) or {}
        res = resp.get("result") or {}
        return {"tools": res.get("tools") or [], "remote_enabled": True}

    def list_prompts(self) -> Dict[str, Any]:
        """
        拉取子进程 MCP 服务器的提示词目录。
        返回: `{"prompts": [...], "remote_enabled": True}`
        """
        rid = str(uuid.uuid4())
        payload = {"jsonrpc": "2.0", "method": "prompts/list", "params": {}, "id": rid}
        resp = self._request(payload) or {}
        res = resp.get("result") or {}
        return {"prompts": res.get("prompts") or [], "remote_enabled": True}

    def list_resources(self) -> Dict[str, Any]:
        """
        拉取子进程 MCP 服务器的资源目录。
        返回: `{"resources": [...], "remote_enabled": True}`
        """
        rid = str(uuid.uuid4())
        payload = {"jsonrpc": "2.0", "method": "resources/list", "params": {}, "id": rid}
        resp = self._request(payload) or {}
        res = resp.get("result") or {}
        return {"resources": res.get("resources") or [], "remote_enabled": True}

    def call_tool(self, name: str, **params) -> Optional[Dict[str, Any]]:
        """
        通过 stdio 执行指定工具。
        返回: `result.data` 或原始结果；失败返回 `None`。
        """
        rid = str(uuid.uuid4())
        payload = {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": name, "arguments": params}, "id": rid}
        resp = self._request(payload)
        if not resp:
            return None
        r = resp.get("result") or {}
        d = r.get("data") if isinstance(r, dict) else None
        return d or r or None

    def close(self) -> None:
        """
        关闭子进程：尽量优雅关闭标准输入并终止进程。
        """
        if self._proc:
            try:
                if self._proc.stdin:
                    try:
                        self._proc.stdin.close()
                    except Exception:
                        pass
                self._proc.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")
    try:
        client = MCPClient()
    except MCPClientError as e:
        print(f"初始化失败: {e}")
        raise SystemExit(1)
    print(json.dumps(client.list_tools(), ensure_ascii=False, indent=2))


__all__ = ["MCPClient", "MCPClientError", "MCPStdioClient"]
