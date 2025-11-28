import json
import logging
import os
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
from http.server import HTTPServer, BaseHTTPRequestHandler
import argparse
import threading

from mcp_client import MCPClient, MCPClientError, MCPStdioClient
from mcp_host import MCPHost


CONFIG_PATH = str(Path(__file__).resolve().parent / "config" / "mcp_server_config.json")
STATIC_DIR = Path(__file__).resolve().parent / "web"
CLIENTS: dict = {}
CLIENT_LOCK = threading.Lock()
TOOL_STATE_PATH = str(Path(__file__).resolve().parent / "config" / "tool_states.json")
HOST: MCPHost = None


def _load_config() -> dict:
    p = Path(CONFIG_PATH)
    if not p.exists():
        return {"mcpServers": {}}
    try:
        try:
            text = p.read_text(encoding="utf-8-sig")
        except Exception:
            text = p.read_text(encoding="utf-8")
        return json.loads(text)
    except Exception:
        return {"mcpServers": {}}


def _save_config(cfg: dict) -> None:
    p = Path(CONFIG_PATH)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_states() -> dict:
    p = Path(TOOL_STATE_PATH)
    if not p.exists():
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{}", encoding="utf-8")
        except Exception:
            pass
        return {}
    try:
        try:
            text = p.read_text(encoding="utf-8-sig")
        except Exception:
            text = p.read_text(encoding="utf-8")
        data = json.loads(text)
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


def _save_states(states: dict) -> None:
    p = Path(TOOL_STATE_PATH)
    p.write_text(json.dumps(states, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_servers(cfg: dict) -> dict:
    servers = {}
    if "servers" in cfg and isinstance(cfg.get("servers"), list):
        for s in cfg["servers"]:
            name = str(s.get("name"))
            if not name:
                continue
            servers[name] = s
    elif "mcpServers" in cfg and isinstance(cfg.get("mcpServers"), dict):
        servers = dict(cfg["mcpServers"])  # shallow copy
    return servers


def _set_server(cfg: dict, name: str, entry: dict) -> None:
    if "servers" in cfg and isinstance(cfg.get("servers"), list):
        found = False
        for i, s in enumerate(cfg["servers"]):
            if str(s.get("name")) == name:
                cfg["servers"][i] = entry
                found = True
                break
        if not found:
            entry_with_name = dict(entry)
            entry_with_name["name"] = name
            cfg["servers"].append(entry_with_name)
    else:
        m = cfg.get("mcpServers") or {}
        m[name] = entry
        cfg["mcpServers"] = m


def _del_server(cfg: dict, name: str) -> None:
    if "servers" in cfg and isinstance(cfg.get("servers"), list):
        cfg["servers"] = [s for s in cfg["servers"] if str(s.get("name")) != name]
    else:
        m = cfg.get("mcpServers") or {}
        if name in m:
            del m[name]
        cfg["mcpServers"] = m


class HostHandler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _bad_request(self, msg: str) -> None:
        self._json(400, {"error": msg})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            index = STATIC_DIR / "index.html"
            if not index.exists():
                self._json(200, {"message": "Host is running", "hint": "Add web/index.html for UI"})
                return
            data = index.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path.endswith(".html"):
            f = STATIC_DIR / parsed.path.lstrip("/")
            if f.exists():
                data = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
        if parsed.path.startswith("/static/"):
            f = STATIC_DIR / parsed.path[len("/static/"):]
            if not f.exists():
                self.send_error(404)
                return
            mime = "text/plain"
            if f.suffix == ".js":
                mime = "application/javascript"
            elif f.suffix == ".css":
                mime = "text/css"
            data = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", f"{mime}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == "/api/servers":
            cfg = _load_config()
            states = _load_states()
            servers = _get_servers(cfg)
            out = []
            for name, entry in servers.items():
                if entry.get("disabled") is not None:
                    enabled = not bool(entry.get("disabled"))
                else:
                    enabled = entry.get("enabled")
                    if enabled is None:
                        enabled = True
                sstate = states.get(name) or {}
                if sstate.get("enabled") is not None:
                    enabled = bool(sstate.get("enabled"))
                out.append({
                    "name": name,
                    "type": entry.get("type"),
                    "url": entry.get("url"),
                    "enabled": enabled,
                    "note": entry.get("note"),
                    "description": entry.get("description") or entry.get("note") or "",
                })
            meta = {"config_path": CONFIG_PATH, "keys": list(cfg.keys()), "mcpServers_count": (len(cfg.get("mcpServers") or {}) if isinstance(cfg.get("mcpServers"), dict) else None)}
            self._json(200, {"servers": out, "meta": meta})
            return
        if parsed.path == "/api/config":
            p = Path(CONFIG_PATH)
            if not p.exists():
                self._json(200, {"path": CONFIG_PATH, "text": json.dumps({"mcpServers": {}}, ensure_ascii=False, indent=2)})
                return
            try:
                try:
                    text = p.read_text(encoding="utf-8-sig")
                except Exception:
                    text = p.read_text(encoding="utf-8")
                self._json(200, {"path": CONFIG_PATH, "text": text})
            except Exception as e:
                self._json(500, {"error": str(e)})
            return
        if parsed.path.startswith("/api/server/") and parsed.path.endswith("/tools"):
            # /api/server/{name}/tools
            name = unquote(parsed.path.split("/")[3])
            cfg = _load_config()
            states = _load_states()
            servers = _get_servers(cfg)
            entry = servers.get(name)
            if entry and entry.get("enabled") is False:
                self._json(400, {"error": "Server disabled"})
                return
            try:
                res = HOST.list_tools(name)
                tools = (res or {}).get("tools") or []
                aug = []
                sstate = states.get(name) or {}
                tstate = (sstate.get("tools") if isinstance(sstate.get("tools"), dict) else {}) or {}
                for t in tools:
                    n = t.get("name")
                    if not n:
                        continue
                    enabled = True
                    if n in tstate:
                        v = tstate.get(n)
                        try:
                            enabled = bool(v.get("turn-on"))
                        except Exception:
                            enabled = True
                    tt = dict(t)
                    tt["enabled"] = bool(enabled)
                    try:
                        note_val = ""
                        if n in tstate and isinstance(tstate.get(n), dict):
                            nv = tstate.get(n) or {}
                            if isinstance(nv.get("note"), str):
                                note_val = nv.get("note") or ""
                        tt["note"] = note_val
                    except Exception:
                        tt["note"] = ""
                    aug.append(tt)
                self._json(200, {"tools": aug, "remote_enabled": (res or {}).get("remote_enabled", True)})
            except MCPClientError as e:
                self._json(500, {"error": str(e)})
            except Exception as e:
                self._json(500, {"error": str(e)})
            return
        if parsed.path.startswith("/api/server/") and parsed.path.endswith("/prompts"):
            name = unquote(parsed.path.split("/")[3])
            cfg = _load_config()
            servers = _get_servers(cfg)
            entry = servers.get(name)
            if entry and entry.get("enabled") is False:
                self._json(400, {"error": "Server disabled"})
                return
            try:
                def _get_client(n, e):
                    ClientCls = MCPStdioClient if (e and str(e.get("type")) == "stdio") else MCPClient
                    with CLIENT_LOCK:
                        c = CLIENTS.get(n)
                        if not c:
                            c = ClientCls(server_name=n, config_path=CONFIG_PATH, timeout=30.0)
                            CLIENTS[n] = c
                        return c
                client = _get_client(name, entry)
                res = client.list_prompts()
                self._json(200, res)
            except MCPClientError as e:
                self._json(500, {"error": str(e)})
            return
        if parsed.path.startswith("/api/server/") and parsed.path.endswith("/resources"):
            name = unquote(parsed.path.split("/")[3])
            cfg = _load_config()
            servers = _get_servers(cfg)
            entry = servers.get(name)
            if entry and entry.get("enabled") is False:
                self._json(400, {"error": "Server disabled"})
                return
            try:
                def _get_client(n, e):
                    ClientCls = MCPStdioClient if (e and str(e.get("type")) == "stdio") else MCPClient
                    with CLIENT_LOCK:
                        c = CLIENTS.get(n)
                        if not c:
                            c = ClientCls(server_name=n, config_path=CONFIG_PATH, timeout=30.0)
                            CLIENTS[n] = c
                        return c
                client = _get_client(name, entry)
                res = client.list_resources()
                self._json(200, res)
            except MCPClientError as e:
                self._json(500, {"error": str(e)})
            return
        if parsed.path.startswith("/api/server/") and parsed.path.endswith("/config"):
            name = unquote(parsed.path.split("/")[3])
            cfg = _load_config()
            servers = _get_servers(cfg)
            entry = servers.get(name)
            if not entry:
                self._json(404, {"error": "Server not found"})
                return
            self._json(200, {"name": name, "entry": entry, "meta": {"config_path": CONFIG_PATH}})
            return
        if parsed.path.startswith("/api/server/") and parsed.path.endswith("/tool-schema"):
            # /api/server/{name}/tool-schema?name=toolName
            name = unquote(parsed.path.split("/")[3])
            q = parse_qs(parsed.query)
            tool_name = (q.get("name") or [None])[0]
            if not tool_name:
                self._bad_request("Missing tool name")
                return
            try:
                cfg = _load_config()
                servers = _get_servers(cfg)
                entry = servers.get(name)
                res = HOST.list_tools(name) or {}
                tools = res.get("tools") or []
                schema = None
                for t in tools:
                    if t.get("name") == tool_name:
                        schema = t
                        break
                if not schema:
                    self._json(404, {"error": "Tool not found"})
                    return
                self._json(200, schema)
            except MCPClientError as e:
                self._json(500, {"error": str(e)})
            return
        if parsed.path.startswith("/api/"):
            self._json(404, {"error": "Not Found"})
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}
        if parsed.path == "/api/tool/call":
            name = payload.get("name") or payload.get("tool")
            params = payload.get("parameters") or payload.get("arguments") or {}
            spec = {"name": name, "parameters": params}
            server = payload.get("server")
            if server:
                spec["server"] = server
            if not name or not isinstance(params, dict):
                self._bad_request("name/tool 与 parameters/arguments 必填")
                return
            try:
                res = HOST.call_tool_by_spec(spec)
                self._json(200, res)
            except MCPClientError as e:
                self._json(500, {"error": str(e)})
            return
        if parsed.path.startswith("/api/server/") and parsed.path.endswith("/call"):
            name = unquote(parsed.path.split("/")[3])
            tool = payload.get("tool") or payload.get("name")
            arguments = payload.get("arguments") or {}
            if not tool or not isinstance(arguments, dict):
                self._bad_request("tool 和 arguments 必填")
                return
            cfg = _load_config()
            servers = _get_servers(cfg)
            entry = servers.get(name)
            if not entry:
                self._json(404, {"error": "Server not found"})
                return
            if entry.get("enabled") is False:
                self._json(400, {"error": "Server disabled"})
                return
            try:
                res_str = HOST.call_server_tool(name, tool, **arguments)
                try:
                    res_obj = json.loads(res_str)
                except Exception:
                    res_obj = res_str
                self._json(200, {"result": res_obj})
            except MCPClientError as e:
                self._json(500, {"error": str(e)})
            return
        if parsed.path.startswith("/api/server/") and parsed.path.endswith("/validate"):
            name = unquote(parsed.path.split("/")[3])
            cfg = _load_config()
            servers = _get_servers(cfg)
            entry = servers.get(name)
            if not entry:
                self._json(404, {"error": "Server not found"})
                return
            try:
                def _get_client(n, e):
                    ClientCls = MCPStdioClient if (e and str(e.get("type")) == "stdio") else MCPClient
                    with CLIENT_LOCK:
                        c = CLIENTS.get(n)
                        if not c:
                            c = ClientCls(server_name=n, config_path=CONFIG_PATH, timeout=30.0)
                            CLIENTS[n] = c
                        return c
                client = _get_client(name, entry)
                res = client.list_tools() or {}
                tools = res.get("tools") or []
                ok = len(tools) > 0
                if ok:
                    self._json(200, {"ok": True, "tools_count": len(tools)})
                else:
                    self._json(200, {"ok": False, "tools_count": 0, "error": "No tools returned"})
            except MCPClientError as e:
                self._json(500, {"error": str(e)})
            return
        if parsed.path == "/api/config":
            text = payload.get("text")
            if not isinstance(text, str):
                self._bad_request("text required")
                return
            try:
                cfg = json.loads(text)
            except Exception:
                self._bad_request("invalid json")
                return
            try:
                _save_config(cfg)
                names = []
                with CLIENT_LOCK:
                    names = list(CLIENTS.keys())
                for n in names:
                    c = None
                    with CLIENT_LOCK:
                        c = CLIENTS.pop(n, None)
                    try:
                        if c and hasattr(c, "close"):
                            c.close()
                    except Exception:
                        pass
                self._json(200, {"ok": True})
            except Exception as e:
                self._json(500, {"error": str(e)})
            return
        if parsed.path == "/api/server/toggle":
            name = payload.get("name")
            enabled = payload.get("enabled")
            if name is None or enabled is None:
                self._bad_request("name and enabled required")
                return
            cfg = _load_config()
            states = _load_states()
            servers = _get_servers(cfg)
            entry = servers.get(name)
            if not entry:
                self._json(404, {"error": "Server not found"})
                return
            entry["enabled"] = bool(enabled)
            _set_server(cfg, name, entry)
            _save_config(cfg)
            sstate = states.get(name) or {}
            sstate["enabled"] = bool(enabled)
            states[name] = sstate
            _save_states(states)
            try:
                if not entry["enabled"]:
                    if HOST:
                        HOST.disable_server(name)
                else:
                    if HOST:
                        HOST.enable_server(name)
            except Exception:
                pass
            self._json(200, {"ok": True})
            return
        if parsed.path.startswith("/api/server/") and parsed.path.endswith("/tools/toggle"):
            # /api/server/{name}/tools/toggle
            name = unquote(parsed.path.split("/")[3])
            tool = payload.get("tool")
            enabled = payload.get("enabled")
            if not tool or enabled is None:
                self._bad_request("tool and enabled required")
                return
            states = _load_states()
            sstate = states.get(name) or {}
            if not isinstance(sstate, dict):
                sstate = {}
            tstate = sstate.get("tools")
            if not isinstance(tstate, dict):
                tstate = {}
            tool = str(tool)
            tstate[tool] = {"turn-on": bool(enabled)}
            sstate["tools"] = tstate
            states[name] = sstate
            _save_states(states)
            self._json(200, {"ok": True, "tool": tool, "enabled": bool(enabled)})
            return
        if parsed.path.startswith("/api/server/") and parsed.path.endswith("/tools/note"):
            name = unquote(parsed.path.split("/")[3])
            tool = payload.get("tool")
            note = payload.get("note")
            if not tool or note is None:
                self._bad_request("tool and note required")
                return
            states = _load_states()
            sstate = states.get(name) or {}
            if not isinstance(sstate, dict):
                sstate = {}
            tstate = sstate.get("tools")
            if not isinstance(tstate, dict):
                tstate = {}
            tool = str(tool)
            entry = tstate.get(tool) or {}
            entry["note"] = str(note)
            tstate[tool] = entry
            sstate["tools"] = tstate
            states[name] = sstate
            _save_states(states)
            self._json(200, {"ok": True})
            return
        if parsed.path == "/api/server/add":
            name = payload.get("name")
            url = payload.get("url")
            if not name or not url:
                self._bad_request("name and url required")
                return
            cfg = _load_config()
            entry = {"type": "streamable-http", "url": url, "enabled": True}
            _set_server(cfg, name, entry)
            _save_config(cfg)
            self._json(200, {"ok": True})
            return
        if parsed.path.startswith("/api/server/") and parsed.path.endswith("/config"):
            name = unquote(parsed.path.split("/")[3])
            cfg = _load_config()
            servers = _get_servers(cfg)
            entry = servers.get(name)
            if not entry:
                entry = {"enabled": True}
            new_name = payload.get("name") or name
            patch = payload.get("entry") or payload
            if isinstance(patch, dict):
                if "description" in patch and not patch.get("note"):
                    patch["note"] = patch.get("description")
                for k, v in patch.items():
                    if k == "name":
                        continue
                    entry[k] = v
            if new_name != name:
                _del_server(cfg, name)
                _set_server(cfg, new_name, entry)
            else:
                _set_server(cfg, name, entry)
            _save_config(cfg)
            c = None
            with CLIENT_LOCK:
                c = CLIENTS.pop(name, None)
                if new_name != name:
                    CLIENTS.pop(new_name, None)
            try:
                if c and hasattr(c, "close"):
                    c.close()
            except Exception:
                pass
            self._json(200, {"ok": True, "name": new_name, "entry": entry})
            return
        if parsed.path.startswith("/api/"):
            self._json(404, {"error": "Not Found"})
        else:
            self.send_error(404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/server/"):
            # /api/server/{name}
            name = unquote(parsed.path.split("/")[3])
            cfg = _load_config()
            _del_server(cfg, name)
            _save_config(cfg)
            try:
                states = _load_states()
                try:
                    if name in states:
                        del states[name]
                except Exception:
                    pass
                try:
                    servers = _get_servers(cfg)
                    if isinstance(states, dict) and isinstance(servers, dict):
                        states = { k: v for k, v in states.items() if k in servers }
                except Exception:
                    pass
                _save_states(states)
            except Exception:
                pass
            c = None
            with CLIENT_LOCK:
                c = CLIENTS.pop(name, None)
            try:
                if c and hasattr(c, "close"):
                    c.close()
            except Exception:
                pass
            self._json(200, {"ok": True})
            return
        self.send_error(404)


def run(host: str = None, port: int = None):
    if host is None:
        host = os.getenv("MCP_HOST_ADDR") or "127.0.0.1"
    if port is None:
        try:
            port = int(os.getenv("MCP_HOST_PORT") or os.getenv("PORT") or 8000)
        except Exception:
            port = 8000
    try:
        logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")
    except Exception:
        pass
    httpd = HTTPServer((host, port), HostHandler)
    print(f"MCP Host running at http://{host}:{port}/")
    try:
        globals()["HOST"] = MCPHost(config_path=CONFIG_PATH, prewarm=False)
        def _prewarm_with_host():
            try:
                HOST.start(prewarm=True)
            except Exception:
                pass
        t = threading.Thread(target=_prewarm_with_host, daemon=True)
        t.start()
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    try:
        p_default = int(os.getenv("MCP_HOST_PORT") or os.getenv("PORT") or 8000)
    except Exception:
        p_default = 8000
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", type=str, default=os.getenv("MCP_HOST_ADDR") or "127.0.0.1")
    parser.add_argument("--port", type=int, default=p_default)
    args = parser.parse_args()
    run(host=args.host, port=args.port)
