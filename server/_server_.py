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


    


if __name__ == '__main__':
    app.run(threaded=True, debug=True)