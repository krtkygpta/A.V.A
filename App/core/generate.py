from core.messageHandler import add_assistant_message, add_message, messages, tools
from core.server_api import generate_remote_response

def generate_response():
    try:
        # Keep the same shape as before, but call generation on the server.
        server_message = generate_remote_response(list(messages), list(tools))
        message = {
            'role': server_message.get('role', 'assistant'),
            'content': server_message.get('content'),
            'tool_calls': server_message.get('tool_calls') or [],
        }
        
        content = message.get('content')
        tool_calls = message.get('tool_calls') or []

        # Preserve combined assistant messages like:
        # "I'll check that for you now." + tool call(s)
        if content is not None or tool_calls:
            add_assistant_message(content=content, tool_calls=tool_calls)
        else:
            add_message(content="I'm not sure how to respond to that.", role='assistant')
            
        return message
        
    except Exception as e:
        error_msg = str(e)
        print(f"[Gen] API error: {error_msg}")
        
        error_response = 'I encountered an error while processing your request. Please try again.'
        add_message(content=error_response, role='assistant')
        
        return {
            'role': 'assistant',
            'content': error_response,
            'error': True,
            'error_details': error_msg
        }
