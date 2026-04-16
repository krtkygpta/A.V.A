import json
import asyncio
from openai import OpenAI
from SmartHome import control_lights, AC_TOOLS, execute_tool

client = OpenAI(
    base_url='http://localhost:11434/v1/',
    api_key='ollama_local',
)

OLLAMA_MODEL = 'gemma4:31b-cloud'
def run_smarthome_agent(command: str) -> str:
    """
    Sub-agent to interpret and execute smart home instructions.
    Uses a local LLM to call the wizlight controls and LG ThinQ AC.
    """
    system_prompt = (
        "You are a Smart Home Controller sub-agent. "
        "Your job is to understand the user's smart home request, call the appropriate tools to control the devices "
        "(like WiZ bulbs and LG ThinQ Air Conditioners), and return a brief summary of the action taken. "
        "CRITICAL: You MUST use the exact tool names provided: 'control_lights' for lights, 'LGTHINQAC' for the AC. "
        "For LGTHINQAC, always include the 'action' field to specify what to do."
    )

    tools = [
        {
            "type": "function",
            "function": {
                "name": "control_lights",
                "description": "Control smart lights (WiZ bulbs).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "Light_name": {
                            "type": "string",
                            "description": "Name of the light, either 'Lights' or 'Lamp'",
                            "enum": ["Lights", "Lamp"]
                        },
                        "action": {
                            "type": "string",
                            "description": "The action to perform on the light.",
                            "enum": ["turn_on", "turn_off", "set"]
                        },
                        "brightness": {
                            "type": "integer",
                            "description": "Brightness level from 1 to 100"
                        },
                        "color": {
                            "type": "array",
                            "description": "RGB tuple for color, like [255, 0, 0] for red. Only used with 'set' action.",
                            "items": {"type": "integer"}
                        }
                    },
                    "required": ["Light_name", "action"]
                }
            }
        },
        {
            "type": "function",
            "function": AC_TOOLS[0]  # The single LGTHINQAC schema entry
        }
    ]

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": command}
    ]

    try:
        response = client.chat.completions.create(
            model=OLLAMA_MODEL,
            messages=messages,
            tools=tools,
            tool_choice="auto"
        )

        response_message = response.choices[0].message

        if response_message.tool_calls:
            assistant_msg = {"role": "assistant", "tool_calls": []}
            if response_message.content:
                assistant_msg["content"] = response_message.content

            for call in response_message.tool_calls:
                assistant_msg["tool_calls"].append({
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments
                    }
                })
            messages.append(assistant_msg)

            for tool_call in response_message.tool_calls:
                func_name = tool_call.function.name
                args = json.loads(tool_call.function.arguments)

                try:
                    if func_name == "control_lights":
                        tool_result_str = control_lights(
                            args.get("Light_name"),
                            args.get("action"),
                            args.get("brightness", 100),
                            args.get("color", [255, 255, 255])
                        )

                    elif func_name == "LGTHINQAC":
                        try:
                            loop = asyncio.new_event_loop()
                            ac_result = loop.run_until_complete(execute_tool("LGTHINQAC", args))
                            loop.close()
                            tool_result_str = json.dumps(ac_result, default=str)
                        except Exception as e:
                            tool_result_str = json.dumps({"error": str(e)})

                    else:
                        tool_result_str = json.dumps({
                            "error": f"Unknown tool '{func_name}'. Use 'control_lights' or 'LGTHINQAC'."
                        })

                except Exception as e:
                    tool_result_str = json.dumps({"error": str(e)})

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": func_name,
                    "content": tool_result_str
                })

            final_response = client.chat.completions.create(
                model=OLLAMA_MODEL,
                messages=messages
            )
            return final_response.choices[0].message.content

        else:
            return response_message.content or "No action taken."

    except Exception as e:
        return json.dumps({"status": "error", "message": f"Smarthome subagent error: {str(e)}"})
if __name__ == "__main__":
    print(run_smarthome_agent(input(">> ")))