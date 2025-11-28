# 预热机制说明

## 预热的目的
- 减少用户首次打开“工具”页面时的等待时间。
- 对本地 `stdio` 类型的 MCP Server，提前启动子进程并完成初始化握手；后续 `tools/list` 直接复用同一进程与管道。
- 跳过远程 HTTP 类型的预热，避免产生有限收益的远程调用与网络等待。

## 如何实现
- 启动阶段创建管理器并触发预热
  - 在 `host_server.py:603-612` 启动 HTTP 服务时，初始化全局 `MCPHost`，并在后台线程调用 `HOST.start(prewarm=True)`。
- 仅预热本地 `stdio` 服务器
  - 在 `mcp_host.py:63-74` 中，根据服务器 `type` 选择客户端；当 `type == 'stdio'` 且 `prewarm=True` 时，调用一次 `list_tools()` 完成预热。
  - 记录预热耗时与工具数量：`mcp_host.py:70-75` 使用 `time.perf_counter()` 统计毫秒耗时，并输出日志。
- 客户端复用与生命周期
  - 预热后，后续接口通过 `MCPHost` 复用已创建的客户端与进程：
    - 列表工具：`host_server.py:220-249` 使用 `HOST.list_tools(name)` 返回工具，并合并启用状态与备注。
    - 调用工具：`host_server.py:376-387` 使用 `HOST.call_tool(name, tool, **arguments)` 执行工具调用。
  - 启用/禁用服务器时，联动 `MCPHost` 管理客户端生命周期：`host_server.py:459-475`。

## 运行日志示例
```
>>> python .\host_server.py
MCP Host running at http://127.0.0.1:8000/
[2025-11-25 15:31:24,253] INFO mcp_host: [Prewarm] skip http server '高德地图'
[2025-11-25 15:31:25,262] INFO mcp_host: [Prewarm] stdio server 'variflight' listing tools...
[2025-11-25 15:31:25,264] INFO mcp_host: [Prewarm] stdio server 'variflight' ready in 2ms, tools=8
[2025-11-25 15:31:25,264] INFO mcp_host: [Prewarm] skip http server 'DIDI'
[2025-11-25 15:31:28,158] INFO mcp_host: [Prewarm] stdio server 'time' listing tools...
[2025-11-25 15:31:28,159] INFO mcp_host: [Prewarm] stdio server 'time' ready in 0ms, tools=2
[2025-11-25 15:31:30,809] INFO mcp_host: [Prewarm] stdio server 'fetch' listing tools...
[2025-11-25 15:31:30,811] INFO mcp_host: [Prewarm] stdio server 'fetch' ready in 1ms, tools=1
[2025-11-25 15:31:31,269] INFO mcp_host: [Prewarm] stdio server 'github' listing tools...
[2025-11-25 15:31:31,276] INFO mcp_host: [Prewarm] stdio server 'github' ready in 7ms, tools=26
```

## 注意事项
- 预热仅针对 `stdio` 类型服务器；HTTP 类型跳过预热。
- 若某服务器未启用，则不会参与预热与客户端创建。
- 日志输出由 `host_server.py:572` 设置的终端日志配置启用；如需调整日志级别与格式，可在此处修改。
