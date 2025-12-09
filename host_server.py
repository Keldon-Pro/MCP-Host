import json
import logging
import os
import threading
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
from http.server import HTTPServer, BaseHTTPRequestHandler
import argparse

from mcp_client import MCPClientError
from mcp_host import MCPHost
import constants

CONFIG_PATH = constants.DEFAULT_CONFIG_PATH
STATIC_DIR = constants.STATIC_DIR
HOST: MCPHost = None

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

    # --- GET Handlers ---

    def handle_index(self, parsed, payload, match):
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

    def handle_html(self, parsed, payload, match):
        f = STATIC_DIR / parsed.path.lstrip("/")
        if not f.exists():
            if parsed.path.startswith("/static/"):
                # Fallback to static handler logic if path also matches /static/
                f2 = STATIC_DIR / parsed.path[len("/static/"):]
                if f2.exists():
                     self._serve_static_file(f2)
                     return
            self.send_error(404)
            return
        
        data = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_static(self, parsed, payload, match):
        # match.group(1) is the part after /static/
        f = STATIC_DIR / match.group(1)
        if not f.exists():
            self.send_error(404)
            return
        self._serve_static_file(f)

    def _serve_static_file(self, f):
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

    def handle_list_servers(self, parsed, payload, match):
        cfg = HOST.get_server_config()
        states = HOST.load_states()
        order = HOST.get_server_order()
        
        servers_map = {}
        m = cfg.get("mcpServers") or {}
        for k, v in m.items():
            servers_map[k] = v

        out = []
        for name, entry in servers_map.items():
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
        # sort by order file
        pos = {n: i for i, n in enumerate(order)}
        out.sort(key=lambda s: (pos.get(s.get("name"), 10**9), s.get("name") or ""))
        meta = {"config_path": CONFIG_PATH, "keys": list(cfg.keys()), "mcpServers_count": (len(cfg.get("mcpServers") or {}) if isinstance(cfg.get("mcpServers"), dict) else None)}
        self._json(200, {"servers": out, "meta": meta})

    def handle_get_server_order(self, parsed, payload, match):
        ord_list = HOST.get_server_order()
        self._json(200, {"order": ord_list})

    def handle_get_config(self, parsed, payload, match):
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

    def handle_list_server_tools(self, parsed, payload, match):
        name = unquote(match.group("name"))
        
        # Check if enabled
        cfg = HOST.get_server_config()
        m = cfg.get("mcpServers") or {}
        entry = m.get(name)
        if entry and entry.get("enabled") is False:
            self._json(400, {"error": "Server disabled"})
            return

        states = HOST.load_states()
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

    def handle_list_server_prompts(self, parsed, payload, match):
        name = unquote(match.group("name"))
        try:
            res = HOST.list_prompts(name)
            self._json(200, res)
        except MCPClientError as e:
            self._json(500, {"error": str(e)})

    def handle_list_server_resources(self, parsed, payload, match):
        name = unquote(match.group("name"))
        try:
            res = HOST.list_resources(name)
            self._json(200, res)
        except MCPClientError as e:
            self._json(500, {"error": str(e)})

    def handle_get_server_config(self, parsed, payload, match):
        name = unquote(match.group("name"))
        cfg = HOST.get_server_config()
        m = cfg.get("mcpServers") or {}
        entry = m.get(name)
        if not entry:
            self._json(404, {"error": "Server not found"})
            return
        self._json(200, {"name": name, "entry": entry, "meta": {"config_path": CONFIG_PATH}})

    def handle_tool_schema(self, parsed, payload, match):
        name = unquote(match.group("name"))
        q = parse_qs(parsed.query)
        tool_name = (q.get("name") or [None])[0]
        if not tool_name:
            self._bad_request("Missing tool name")
            return
        try:
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

    def handle_api_404(self, parsed, payload, match):
        self._json(404, {"error": "Not Found"})

    # --- POST Handlers ---

    def handle_call_tool(self, parsed, payload, match):
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
            res_str = HOST.call_tool(spec)
            try:
                res_obj = json.loads(res_str)
            except Exception:
                res_obj = res_str
            self._json(200, res_obj)
        except MCPClientError as e:
            self._json(500, {"error": str(e)})

    def handle_call_server_tool(self, parsed, payload, match):
        name = unquote(match.group("name"))
        tool = payload.get("tool") or payload.get("name")
        arguments = payload.get("arguments") or {}
        if not tool or not isinstance(arguments, dict):
            self._bad_request("tool 和 arguments 必填")
            return
        
        cfg = HOST.get_server_config()
        m = cfg.get("mcpServers") or {}
        entry = m.get(name)
        
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

    def handle_validate_server(self, parsed, payload, match):
        name = unquote(match.group("name"))
        # 使用 HOST.list_tools 来验证，它会自动尝试连接
        try:
            res = HOST.list_tools(name) or {}
            tools = res.get("tools") or []
            ok = len(tools) > 0
            if ok:
                self._json(200, {"ok": True, "tools_count": len(tools)})
            else:
                self._json(200, {"ok": False, "tools_count": 0, "error": "No tools returned"})
        except MCPClientError as e:
            self._json(500, {"error": str(e)})

    def handle_save_config(self, parsed, payload, match):
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
            HOST.save_server_config(cfg)
            self._json(200, {"ok": True})
        except Exception as e:
            self._json(500, {"error": str(e)})

    def handle_toggle_server(self, parsed, payload, match):
        name = payload.get("name")
        enabled = payload.get("enabled")
        if name is None or enabled is None:
            self._bad_request("name and enabled required")
            return
        
        cfg = HOST.get_server_config()
        m = cfg.get("mcpServers") or {}
        entry = m.get(name)
        if not entry:
            self._json(404, {"error": "Server not found"})
            return
        
        # 更新配置
        entry["enabled"] = bool(enabled)
        m[name] = entry
        cfg["mcpServers"] = m # 确保更新回去
        HOST.save_server_config(cfg)
        
        # 更新状态文件
        states = HOST.load_states()
        sstate = states.get(name) or {}
        sstate["enabled"] = bool(enabled)
        states[name] = sstate
        HOST.save_states(states)
        
        # 实时生效
        try:
            if not enabled:
                HOST.disable_server(name)
            else:
                HOST.enable_server(name)
        except Exception:
            pass
        self._json(200, {"ok": True})

    def handle_toggle_tool(self, parsed, payload, match):
        name = unquote(match.group("name"))
        tool = payload.get("tool")
        enabled = payload.get("enabled")
        if not tool or enabled is None:
            self._bad_request("tool and enabled required")
            return
        states = HOST.load_states()
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
        HOST.save_states(states)
        self._json(200, {"ok": True, "tool": tool, "enabled": bool(enabled)})

    def handle_set_tool_note(self, parsed, payload, match):
        name = unquote(match.group("name"))
        tool = payload.get("tool")
        note = payload.get("note")
        if not tool or note is None:
            self._bad_request("tool and note required")
            return
        states = HOST.load_states()
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
        HOST.save_states(states)
        self._json(200, {"ok": True})

    def handle_add_server(self, parsed, payload, match):
        name = payload.get("name")
        url = payload.get("url")
        if not name or not url:
            self._bad_request("name and url required")
            return
        cfg = HOST.get_server_config()
        entry = {"type": "streamable http", "url": url, "enabled": True}
        m = cfg.setdefault("mcpServers", {})
        m[name] = entry
        HOST.save_server_config(cfg)
        try:
            order = HOST.get_server_order()
            if name not in order:
                order.append(str(name))
                HOST.save_server_order(order)
        except Exception:
            pass
        self._json(200, {"ok": True})

    def handle_save_server_order(self, parsed, payload, match):
        order = payload.get("order")
        if not isinstance(order, list):
            self._bad_request("order must be list")
            return
        cfg = HOST.get_server_config()
        servers = cfg.get("mcpServers") or {}
        names = set(servers.keys())
        # keep only known names, preserve given sequence
        new_order = [str(n) for n in order if str(n) in names]
        # append any missing servers at the end
        for n in servers.keys():
            if n not in new_order:
                new_order.append(str(n))
        HOST.save_server_order(new_order)
        self._json(200, {"ok": True, "order": new_order})

    def handle_update_server_config(self, parsed, payload, match):
        name = unquote(match.group("name"))
        cfg = HOST.get_server_config()
        servers = cfg.get("mcpServers") or {}
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
            if name in servers:
                del servers[name]
            servers[new_name] = entry
        else:
            servers[name] = entry
        HOST.save_server_config(cfg)
        try:
            # reflect rename in order file
            if new_name != name:
                order = HOST.get_server_order()
                order = [new_name if x == name else x for x in order]
                # ensure new_name present at least once
                if new_name not in order:
                    order.append(new_name)
                HOST.save_server_order(order)
        except Exception:
            pass
        self._json(200, {"ok": True, "name": new_name, "entry": entry})

    # --- DELETE Handlers ---

    def handle_delete_server(self, parsed, payload, match):
        name = unquote(match.group("name"))
        cfg = HOST.get_server_config()
        servers = cfg.get("mcpServers") or {}
        if name in servers:
            del servers[name]
            HOST.save_server_config(cfg)
        try:
            states = HOST.load_states()
            try:
                if name in states:
                    del states[name]
                    HOST.save_states(states)
            except Exception:
                pass
            try:
                order = HOST.get_server_order()
                order = [x for x in order if x != name]
                HOST.save_server_order(order)
            except Exception:
                pass
        except Exception:
            pass
        self._json(200, {"ok": True})

    # --- Router ---

    ROUTES = [
        # GET Routes
        ("GET", r"^/$", "handle_index"),
        ("GET", r"^/index\.html$", "handle_index"),
        ("GET", r"^/api/servers$", "handle_list_servers"),
        ("GET", r"^/api/servers/order$", "handle_get_server_order"),
        ("GET", r"^/api/config$", "handle_get_config"),
        ("GET", r"^/api/server/(?P<name>[^/]+)/tools$", "handle_list_server_tools"),
        ("GET", r"^/api/server/(?P<name>[^/]+)/prompts$", "handle_list_server_prompts"),
        ("GET", r"^/api/server/(?P<name>[^/]+)/resources$", "handle_list_server_resources"),
        ("GET", r"^/api/server/(?P<name>[^/]+)/config$", "handle_get_server_config"),
        ("GET", r"^/api/server/(?P<name>[^/]+)/tool-schema$", "handle_tool_schema"),
        # Static files last (wildcards)
        ("GET", r".*\.html$", "handle_html"),
        ("GET", r"^/static/(.*)$", "handle_static"),
        ("GET", r"^/api/.*", "handle_api_404"),
        
        # POST Routes
        ("POST", r"^/api/tool/call$", "handle_call_tool"),
        ("POST", r"^/api/server/toggle$", "handle_toggle_server"),
        ("POST", r"^/api/server/add$", "handle_add_server"),
        ("POST", r"^/api/servers/order$", "handle_save_server_order"),
        ("POST", r"^/api/config$", "handle_save_config"),
        ("POST", r"^/api/server/(?P<name>[^/]+)/call$", "handle_call_server_tool"),
        ("POST", r"^/api/server/(?P<name>[^/]+)/validate$", "handle_validate_server"),
        ("POST", r"^/api/server/(?P<name>[^/]+)/tools/toggle$", "handle_toggle_tool"),
        ("POST", r"^/api/server/(?P<name>[^/]+)/tools/note$", "handle_set_tool_note"),
        ("POST", r"^/api/server/(?P<name>[^/]+)/config$", "handle_update_server_config"),
        ("POST", r"^/api/.*", "handle_api_404"),

        # DELETE Routes
        ("DELETE", r"^/api/server/(?P<name>[^/]+)$", "handle_delete_server"),
    ]

    def _dispatch(self, method):
        parsed = urlparse(self.path)
        path = parsed.path
        
        payload = {}
        if method == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            if length > 0:
                body = self.rfile.read(length).decode("utf-8")
                try:
                    payload = json.loads(body)
                except Exception:
                    payload = {}
        
        for route_method, pattern, handler_name in self.ROUTES:
            if route_method == method:
                match = re.match(pattern, path)
                if match:
                    handler = getattr(self, handler_name)
                    try:
                        handler(parsed, payload, match)
                    except Exception as e:
                        self._json(500, {"error": str(e)})
                    return
        
        self.send_error(404)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")
    
    def do_DELETE(self):
        self._dispatch("DELETE")

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
