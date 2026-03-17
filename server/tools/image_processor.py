import ollama
import json
def image_tool(image_path, query): 
    res = ollama.chat(
        model="moondream",
        messages=[
            {
                'role': 'user',
                'content': query,
                'images': [image_path]
            }
        ]
    )
    return json.dumps({'status': 'success', 'message': res['message']['content']})
