from openai import OpenAI
from core.messageHandler import messages, tools, add_message
import os
import dotenv

dotenv.load_dotenv()
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL")
)

model = os.getenv("MODEL_NAME")


def generate_response():
    try:
        # Ensure tools is a valid list and each tool has a name
        valid_tools = []
        if tools and isinstance(tools, list):
            for tool in tools:
                if isinstance(tool, dict) and tool.get('type') == 'function' and tool.get('function', {}).get('name'):
                    valid_tools.append(tool)
        
        # Build context-enriched messages:
        # Inject past conversation summaries (last hour only) so the LLM has
        # awareness of recent interactions without overloading context.
        # Note: Memories are NOT injected here — the LLM retrieves them
        # on demand via the memory_manager tool call.
        enriched_messages = list(messages)  # shallow copy to avoid mutating the original
        
        # Find where to insert context (right after the system prompt)
        insert_idx = 1 if enriched_messages and enriched_messages[0].get('role') == 'system' else 0
        
        # Inject conversation history from the past hour only
        try:
            from knowledge.ConversationManager import get_recent_hour_conversations_context
            convo_context = get_recent_hour_conversations_context()
            if convo_context:
                enriched_messages.insert(insert_idx, {
                    'role': 'system',
                    'content': convo_context
                })
        except Exception:
            pass  # Don't break generation if conversation retrieval fails
        
        response = client.chat.completions.create(
            model=model,
            messages=enriched_messages,
            tools=valid_tools if valid_tools else None,
            tool_choice="auto" if valid_tools else None
        )
        
        message = response.choices[0].message.__dict__
        
        # Append response to messages history AND save to conversation
        if content := message.get('content'):
            # Use add_message to save to both messages list and ConversationManager
            add_message(content=content, role='assistant')
        elif tool_calls := message.get('tool_calls'):
            if tool_calls:
                tool_calls_payload = []
                for tc in tool_calls:
                    try:
                        fn = getattr(tc, 'function', None)
                        tool_calls_payload.append({
                            'id': getattr(tc, 'id', ''),
                            'type': 'function',
                            'function': {
                                'name': getattr(fn, 'name', '') if fn else '',
                                'arguments': getattr(fn, 'arguments', '') if fn else ''
                            }
                        })
                    except Exception:
                        continue
                messages.append({'role': 'assistant', 'tool_calls': tool_calls_payload})
        else:
            add_message(content="I'm not sure how to respond to that.", role='assistant')
            
        return message
        
    except Exception as e:
        error_msg = str(e)
        print(f"[DEBUG] API Error: {error_msg}")
        
        error_response = 'I encountered an error while processing your request. Please try again.'
        add_message(content=error_response, role='assistant')
        
        return {
            'role': 'assistant',
            'content': error_response,
            'error': True,
            'error_details': error_msg
        }