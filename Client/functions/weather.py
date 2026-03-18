import subprocess
import requests
from geopy.geocoders import Nominatim
import os
import json
import dotenv

dotenv.load_dotenv()

def get_gps_location():
    try:
        # PowerShell script to get GPS location
        ps_script = """
        Add-Type -AssemblyName System.Device
        $GeoWatcher = New-Object System.Device.Location.GeoCoordinateWatcher
        $GeoWatcher.Start()

        while (($GeoWatcher.Status -ne "Ready") -and ($GeoWatcher.Permission -ne "Denied")) {
            Start-Sleep -Milliseconds 100
        }

        if ($GeoWatcher.Position.Location.IsUnknown) {
            Write-Host "Unknown location"
        } else {
            $latitude = $GeoWatcher.Position.Location.Latitude
            $longitude = $GeoWatcher.Position.Location.Longitude
            Write-Host "$latitude,$longitude"
        }
        """

        # Run the PowerShell script using subprocess
        result = subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-Command", ps_script], capture_output=True, text=True)

        # Check for errors
        if result.returncode != 0:
            return f"Error: {result.stderr.strip()}"

        # Extract latitude and longitude from the result
        output = result.stdout.strip()
        if output == "Unknown location":
            return "Location services are unavailable or location is unknown."

        lat, lng = map(float, output.split(','))

        # Use geopy to reverse geocode to the city name
        geolocator = Nominatim(user_agent="gps_city_locator")
        location = geolocator.reverse((lat, lng), language='en')

        if location and 'city' in location.raw['address']:
            return location.raw['address']['city']
        elif location and 'town' in location.raw['address']:
            return location.raw['address']['town']
        elif location and 'village' in location.raw['address']:
            return location.raw['address']['village']
        else:
            return "City not found"
    except Exception as e:
        return f"Error: {str(e)}"

def get_weather(location: str):
    try:
        if location == 'current':
            location = get_gps_location()  # Assuming this function exists and gets the GPS location

        params = ['temperature', 'humidity', 'wind_speed', 'description', 'clouds', 'precipitation']
        return get_weather_(location, params)
    except Exception as e:
        # Catch any unexpected errors
        print(f"Unexpected error: {e}")
        return {"error": "An unexpected error occurred, please try again"}

# print(get_weather(location='greater noida'))
def get_weather_(location, params=None):
    if params is None:
        params = ['temperature', 'humidity', 'wind_speed', 'description', 'clouds', 'precipitation']
    api_key=os.getenv("WEATHER_API_KEY")
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