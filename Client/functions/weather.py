import subprocess
import requests
from geopy.geocoders import Nominatim

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

        url = 'http://127.0.0.1:5000/edith/tools'
        data = {
            'tool': 'weather',
            'location': location
        }

        response = requests.post(url, json=data)
        response.raise_for_status()  # Raise an exception for HTTP errors

        # Validate the response JSON
        try:
            result = response.json()
            if not result:  # Check if JSON is empty
                return {"error": "Invalid response received, please try again"}
            return result
        except ValueError:
            return {"error": "Invalid JSON response, please try again"}

    except requests.exceptions.RequestException as e:
        # Catch network-related errors (e.g., connection issues)
        print(f"Network error: {e}")
        return {"error": "Error, please try again"}
    except Exception as e:
        # Catch any unexpected errors
        print(f"Unexpected error: {e}")
        return {"error": "An unexpected error occurred, please try again"}

# print(get_weather(location='greater noida'))
