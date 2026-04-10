import os
import json
from dotenv import load_dotenv
from openai import OpenAI
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
    provider = (os.getenv("LLM_PROVIDER") or "openai").strip().lower()
    mode = (os.getenv("TOOL_CALL_MODE") or "native").strip().lower()

    # --- 优先演示原生 function calling ---
    if mode == "native":
        sys_prompt = "你是人工智能助手。可使用 MCP 工具。请在需要时主动调用工具，并根据工具结果给出中文答案。"
        if provider == "gemini":
            llm_tools = host.tools_for_gemini(tools)
        elif provider == "qwen":
            llm_tools = host.tools_for_qwen(tools)
        elif provider == "deepseek":
            llm_tools = host.tools_for_deepseek(tools)
        else:
            llm_tools = host.tools_for_openai(tools)

        first = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_msg},
            ],
            tools=llm_tools,
            tool_choice="auto",
        )
        msg = first.choices[0].message
        calls = host.detect_native_tool_calls(provider, msg)
        if calls:
            print("\nASSISTANT > 原生工具调用\n")
            print(json.dumps(calls, ensure_ascii=False, indent=2))
            call_results = host.call_native_tool_calls(calls, formated=True)
            for it in call_results:
                print("\nTOOL_RESULT >\n")
                print(it["result_json"])

            # OpenAI-compatible tool result messages
            tool_messages = []
            for it in call_results:
                tool_call_id = it.get("id")
                payload = it.get("result_json") or "{}"
                if tool_call_id:
                    tool_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": payload,
                    })
                else:
                    tool_messages.append({
                        "role": "system",
                        "content": "<tool_result>" + payload + "</tool_result>",
                    })

            second = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_msg},
                    {
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": getattr(msg, "tool_calls", None),
                    },
                    *tool_messages,
                ],
            )
            print("\nASSISTANT > " + (second.choices[0].message.content or "") + "\n")
            return
        print("\nASSISTANT > " + (msg.content or "") + "\n")
        return

    # --- 兼容旧版：文本工具调用 ---
    sys_prompt = (
        "你是人工智能助手。可使用 MCP 工具。若需要调用工具，"
        "请仅输出如下格式文本：<tool>{\n\t\"type\": \"function\",\n\t\"name\": \"<工具名>\",\n\t\"parameters\": {…}\n}</tool>。"
        "以下为各工具的使用说明：\n" + host.tools_guide(tools)
    )
    first = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_msg},
        ],
    )
    content = first.choices[0].message.content or ""
    has_tool, spec = host.detect_tool(content)
    if not has_tool:
        print("\nASSISTANT > " + content + "\n")
        return
    print("\nASSISTANT > 生成的工具调用\n")
    print(json.dumps(spec, ensure_ascii=False, indent=2))
    tool_result = host.call_tool(spec, formated=True)
    print("\nTOOL_RESULT >\n")
    print(tool_result)
    second = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": content},
            {"role": "system", "content": "<tool_result>" + tool_result + "</tool_result> 请基于工具结果用中文回复用户。"},
        ],
    )
    print("\nASSISTANT > " + (second.choices[0].message.content or "") + "\n")

if __name__ == "__main__":
    main()
