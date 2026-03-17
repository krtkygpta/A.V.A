# import ollama 
from flask import Flask, request, jsonify
from tools import get_time_data, weather_retrieve, image_processor
from tools.get_song_url import song_url 
# from groq import Groq
app = Flask(__name__)
import openai
import ast
DATA_DIR = "__DATA__"
@app.route('/edith/imagetool', methods=['POST'])
def imagetool():
    data = request.form
    image_file = request.files['image']
    image_path = './received_images/' + str(image_file.filename) # image_file.filename
    image_file.save(image_path)
    response = image_processor.image_tool(image_path=image_path, query=data.get('query'))
    return jsonify(response)


@app.route('/edith/tools', methods=['POST'])
def tools():
    data = request.json
    tool = data.get('tool')
    print((data))
    if tool == "song_url":
        print(data)
        url = song_url(data.get('song_title'))
        return jsonify(url)
    elif tool == 'weather':
        weather_data = weather_retrieve.get_weather(location=data.get('location'), params = ["temperature", "humidity", "wind_speed", "description", "clouds", "precipitation", "rain"])
        return jsonify(weather_data)
    elif tool == 'datetime':
        datetime = get_time_data.get_time_date()
        return jsonify(datetime)
    elif tool == 'image_tool':
        image_file = request.files['image']
        image_path = './received_images/' + str(image_file.filename)
        image_file.save(image_path)
        response = image_processor.image_tool(image_path=image_path, query=data.get('query'))
        return jsonify(response)
    return(jsonify(""))


    

@app.route('/edith/generate', methods=['POST'])
def get_response():
    data = request.json
    tools = data.get('tools')
    messages = data.get('messages')

    # Generate response using the model
    client = openai.OpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key="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")
    
    response = client.chat.completions.create(
        model="llama-3.2-90b-text-preview",
        messages=messages,
        tools=tools,
        tool_choice='auto',
        temperature=0.5,
        max_tokens=2048

    )
    response_message = response.choices[0].message
    if response_message.tool_calls:
        tool_call = response.choices[0].message.tool_calls[0].function
        arguments = ast.literal_eval(tool_call.arguments)
        tool_id = response_message.tool_calls[0].id
        return jsonify({
            'tool_call': True,
            'function': tool_call.name,
            'args': arguments,
            'tool_id': tool_id
        })
    else:
        return jsonify({
            'tool_call': False,
            'content': response_message.content
        })

if __name__ == '__main__':
    app.run(threaded=True, debug=True)