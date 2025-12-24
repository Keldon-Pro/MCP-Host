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

def main():
    host = MCPHost(prewarm=True)
    tools = host.list_all_tools()
    guide = host.tools_guide(tools) if tools else ""

    sys_prompt = (
        "你是人工智能助手。可使用 MCP 工具。若需要调用工具，"
        "请仅输出如下格式文本：\n<tool>{\n\t\"type\": \"function\",\n\t\"name\": \"<工具名>\",\n\t\"parameters\": {…}\n}</tool>\n"
        "当认为信息已充分可回答时，仅输出如下格式文本：<final>...</final>。"
        "如果用户问题信息不全，请直接向用户请求补充所需信息，不要调用工具。"
        "以下为各工具的使用说明：\n" + guide
    )
    print(sys_prompt)
    
    while True:
        user_msg = input("请输入消息 (输入 Exit 结束): ").strip()
        if user_msg.lower() == "exit":
            break
        print(f"\nUSER > {user_msg}\n")

        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_msg},
        ]

        results = []
        step = 0
        max_steps = 5

        first = client.chat.completions.create(model=model, messages=messages)
        content = first.choices[0].message.content or ""

        while step < max_steps:
            print("\nASSISTANT > " + content + "\n")

            has_tool, spec = host.detect_tool(content) 
            if not has_tool:
                break

            print("\nASSISTANT > 生成的工具调用\n")
            print(json.dumps(spec, ensure_ascii=False, indent=2))
            tool_result = host.call_tool(spec, formated=True)
            results.append("<tool_result>" + tool_result + "</tool_result>")
            print("\nTOOL_RESULT >\n")
            print(tool_result)

            messages = [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_msg},
                {"role": "assistant", "content": content},
                {"role": "system", "content": "".join(results) +  " 若信息不足，请继续输出工具调用；若信息充分，请按如下格式输出（<final> 后需空行）：\n<final>\n\n中文回复内容\n</final>\n并基于工具结果用中文回复；若用户问题信息不全，请直接向用户说明需要哪些补充信息。"},
            ]
            second = client.chat.completions.create(model=model, messages=messages)
            content = second.choices[0].message.content or ""
            step += 1

        if step >= max_steps:
            print("\nASSISTANT > " + content + "\n")

if __name__ == "__main__":
    main()
