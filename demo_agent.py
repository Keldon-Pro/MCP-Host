import os
import json
from dotenv import load_dotenv
from openai import OpenAI
from mcp_host import MCPHost

load_dotenv(override=False)

base = os.getenv("LLM_BASE_URL")
api_key = os.getenv("LLM_API_KEY")
model = os.getenv("LLM_MODEL")
provider = (os.getenv("LLM_PROVIDER") or "openai").strip().lower()

client = OpenAI(base_url=base, api_key=api_key)


def _call_tool_and_collect(host: MCPHost, calls):
    results = []
    for call in calls:
        spec = {"name": call.get("name"), "parameters": call.get("parameters") or {}}
        tool_result = host.call_tool(spec, formated=False)
        try:
            parsed = json.loads(tool_result)
        except Exception:
            parsed = tool_result
        results.append({"id": call.get("id"), "name": call.get("name"), "result": parsed})
    return results


# 演示：优先使用原生 function calling；仅在 provider 不支持时退回文本协议
def main():
    host = MCPHost(prewarm=True)
    tools = host.list_all_tools()

    print("\nSYSTEM > 已启用的 MCP 服务器与工具\n")
    if tools:
        print(host.tools_guide(tools))

    user_msg = input("请输入消息: ").strip()
    print(f"\nUSER > {user_msg}\n")

    # 1) 原生 function calling：OpenAI/Qwen/DeepSeek/Gemini
    if provider in {"openai", "qwen", "deepseek", "gemini"}:
        native_tools = host.build_native_tools_payload(provider)
        messages = [
            {"role": "system", "content": "你是人工智能助手，尽可能通过工具完成用户请求。"},
            {"role": "user", "content": user_msg},
        ]
        first = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=native_tools if provider != "gemini" else None,
        )

        calls = host.parse_native_tool_calls(provider, first)
        if calls:
            print("\nASSISTANT > 生成的原生工具调用\n")
            print(json.dumps(calls, ensure_ascii=False, indent=2))
            results = _call_tool_and_collect(host, calls)
            for item in results:
                print("\nTOOL_RESULT >\n")
                print(json.dumps(item, ensure_ascii=False, indent=2))

            tool_msgs = host.build_native_tool_result_messages(provider, results)
            # OpenAI/Qwen/DeepSeek: 继续二轮对话
            if provider in {"openai", "qwen", "deepseek"}:
                second_messages = messages + [{"role": "assistant", "content": first.choices[0].message.content or "", "tool_calls": first.choices[0].message.tool_calls}] + tool_msgs
                second = client.chat.completions.create(model=model, messages=second_messages)
                print("\nASSISTANT > " + (second.choices[0].message.content or "") + "\n")
                return

        content = first.choices[0].message.content or ""
        print("\nASSISTANT > " + content + "\n")
        return

    # 2) 退回文本工具协议
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
    if has_tool:
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
    else:
        print("\nASSISTANT > " + content + "\n")


if __name__ == "__main__":
    main()
