import os
import json
import re
from dotenv import load_dotenv
from openai import OpenAI
from pathlib import Path
from typing import Dict, Any
from mcp_host import MCPHost

load_dotenv(override=False)

base = os.getenv("LLM_BASE_URL")
api_key = os.getenv("LLM_API_KEY")
model = os.getenv("LLM_MODEL")

client = OpenAI(base_url=base, api_key=api_key)

# 演示：使用 MCP Host 结合大模型进行工具调用与对话
def main():
    # 初始化 Host 管理器：负责聚合 MCP 服务器工具目录、生成参数指南并路由真实调用
    host = MCPHost(prewarm=True)
    print("\nSYSTEM > 已启用的 MCP 服务器与工具\n")
    # 拉取所有启用服务器的工具，并结合状态文件过滤掉关闭的工具
    tools = host.list_all_tools()
    if tools:
        # 基于工具的 JSON Schema/参数列表生成可读的参数指南，帮助 LLM 正确填参
        guide = host.tools_guide(tools)
        print(guide)

    # 读取用户输入并打印到控制台，便于观察交互内容
    user_msg = input("请输入消息: ").strip()
    print(f"\nUSER > {user_msg}\n")
    sys_prompt = (
        "你是人工智能助手。可使用 MCP 工具。若需要调用工具，"
        "请仅输出如下格式文本：<tool>{\n\t\"type\": \"function\",\n\t\"name\": \"<工具名>\",\n\t\"parameters\": {…}\n}</tool>。"
        "以下为各工具的使用说明：\n" + host.tools_guide(tools)
    )

    # 第一段对话：请求 LLM 决定是否输出 <tool> 调用契约
    first = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_msg},
        ],
    )
    # 提取首次回复文本
    content = first.choices[0].message.content or ""

    has_tool, spec = host.detect_tool(content)
    if has_tool:
        print("\nASSISTANT > 生成的工具调用\n")
        print(json.dumps(spec, ensure_ascii=False, indent=2))
        tool_result = host.call_tool(spec,formated=True)
        print("\nTOOL_RESULT >\n")
        print(tool_result)


        # 第二段对话：
        # - 注入完整工具结果到 <tool_result> 标签
        # - 要求模型基于工具结果用中文回复用户
        second = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_msg},
                {"role": "assistant", "content": content},
                {"role": "system", "content": "<tool_result>" + tool_result + "</tool_result> 请基于工具结果用中文回复用户。"},
            ],
        )
        # 打印最终助手回复
        print("\nASSISTANT > " + (second.choices[0].message.content or "") + "\n")
    else:
        # 若未生成工具契约，直接输出首次回复
        print("\nASSISTANT > " + content + "\n")

if __name__ == "__main__":
    main()
