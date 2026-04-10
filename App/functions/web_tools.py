import os
import json
import subprocess
import requests
import re
from geopy.geocoders import Nominatim
from googlesearch import search
from bs4 import BeautifulSoup
from google import genai
from google.genai.types import Tool, GenerateContentConfig, GoogleSearch

# Common initialization for settings env vars
_settings_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'settings.json')
try:
    with open(_settings_path, 'r') as _f:
        os.environ.update({k: str(v) for k, v in json.load(_f).items()})
except Exception:
    pass

# Singleton Gemini client for AI tasks
_client = genai.Client(api_key=os.getenv("GOOGLE_AI_API_KEY"))

# -----------------
# 1. WEATHER & GPS
# -----------------
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

        if result.returncode != 0:
            return f"Error: {result.stderr.strip()}"

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
            location = get_gps_location()

        params = ['temperature', 'humidity', 'wind_speed', 'description', 'clouds', 'precipitation']
        return get_weather_(location, params)
    except Exception as e:
        print(f"[Weather] Unexpected error: {e}")
        return {"error": "An unexpected error occurred, please try again"}

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
            "precipitation": current["precip_mm"],
            "rain": current.get("precip_mm", 0)
        }
        filtered_info = {key: weather_info[key] for key in params if key in weather_info}
        filtered_info = json.dumps({
            'success': 'success',
            'message': f'weather report is {filtered_info}'
        })
        return filtered_info
    else:
        return {"error": "Location not found"}

# -----------------
# 2. WEB SCRAPING
# -----------------
def fetch_website_data(url: str) -> str:
    """Fetch raw HTML/text content from a URL."""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return json.dumps({'status': 'success', 'content': response.text[:5000]})
    except requests.exceptions.RequestException as e:
        return json.dumps({'status': 'error', 'content': f"Error: {e}"})

def google_search(query, num_results=5):
    """Perform a Google search and return list of URLs."""
    try:
        search_results = search(query, num_results=num_results)
        results = list(search_results)
        return results
    except Exception as e:
        return []

def extracttext(html: str) -> str:
    """Extract clean text from HTML content."""
    try:
        soup = BeautifulSoup(html, 'html.parser')
        body_content = soup.body
        if body_content:
            clean_text = re.sub(r'<[^>]+>', '', body_content.get_text())
            clean_text = clean_text.replace("\n", " ")
            clean_text = re.sub(r'\s+', ' ', clean_text).strip()
            return clean_text
        return ""
    except Exception:
        return ""

def webscraper(links: list) -> list:
    """Scrape text content from a list of URLs."""
    final_container = []
    for url in links:
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                text = extracttext(response.text)
                if text:
                    final_container.append(text[:2000])
        except Exception as e:
            print(f"Error scraping {url}: {e}")
            continue
    return final_container

def webdata(query):
    """Search the web and scrape results for a query."""
    links = google_search(query=query, num_results=5)
    if not links:
        return json.dumps({'status': 'error', 'content': 'No search results found'})
    data = webscraper(links=links)
    if not data:
        return json.dumps({'status': 'error', 'content': 'Could not extract data from search results'})
    return json.dumps({'status': 'success', 'content': data})

# -----------------
# 3. GOOGLE AI SEARCH
# -----------------
def get_google_ai_response(query: str) -> str:
    """
    Get AI-generated response using Google's Gemini model with search capability
    """
    model_id = "gemma-4-26b-a4b-it"
    google_search_tool = Tool(
        google_search = GoogleSearch()
    )

    response = _client.models.generate_content(
        model=model_id,
        contents=query,
        config=GenerateContentConfig(
            tools=[google_search_tool],
            response_modalities=["TEXT"],
            max_output_tokens=2048
        )
    )
    
    return json.dumps({'status': 'success', 'content': ' '.join(part.text for part in response.candidates[0].content.parts)}) # type: ignore
