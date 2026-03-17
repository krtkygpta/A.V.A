import requests
import json

def get_weather(location, params):
    api_key='e51386c94bd14e5c9f192423241110'
    base_url = "http://api.weatherapi.com/v1/current.json"
    complete_url = f"{base_url}?key={api_key}&q={location}&aqi=no"
    
    response = requests.get(complete_url)
    data = response.json()
    
    if "error" not in data:
        current = data["current"]
        
        weather_info = {
            "location": data["location"]["name"],
            "temperature": current["temp_c"],
            "humidity": current["humidity"],
            "wind_speed": current["wind_kph"],
            "description": current["condition"]["text"],
            "clouds": current["cloud"],
            "precipitation": current["precip_mm"],  # Precipitation in mm
            "rain": current.get("precip_mm", 0)  # Rain volume in mm
        }
        
        # Filter the weather info based on requested parameters
        filtered_info = {key: weather_info[key] for key in params if key in weather_info}
        filtered_info = json.dumps({
            'success': 'success',
            'message': f'weather report is {filtered_info}'
        })
        return filtered_info
    else:
        return {"error": "Location not found"}

# Example usage
if __name__ == "__main__":
    # API_KEY = "your_api_key_here"  # Replace with your WeatherAPI key
    location = "noida, india"
    params = ["temperature", "humidity", "wind_speed", "description", "clouds", "precipitation", "rain"]
    
    weather_data = get_weather(location, params)
    print(weather_data)

