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

DEFAULT_TIMEOUT = 30

_settings_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'settings.json')
try:
    with open(_settings_path, 'r') as _f:
        os.environ.update({k: str(v) for k, v in json.load(_f).items()})
except Exception:
    pass

SERVER_URL = os.getenv("SERVER_URL")


def _google_search(query, num_results=5):
    """Perform a Google search and return list of URLs."""
    try:
        results = search(query, num_results=num_results)
        return list(results)
    except Exception:
        return []


def _extract_text(html: str) -> str:
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


def _scrape_urls(links: list) -> list:
    """Scrape text content from a list of URLs."""
    results = []
    for url in links:
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                text = _extract_text(response.text)
                if text:
                    results.append({"url": url, "text": text[:2000]})
        except Exception:
            continue
    return results


def web(operation: str, **kwargs):
    """Unified web operations: search, fetch URLs, AI search, weather, GPS.
    
    Args:
        operation: 'search', 'fetch', 'ai_search', 'weather', 'gps'
        
    For operation='search':
        - query (str): What to search for (e.g. 'Python tutorial')
        - num_results (int): Number of results (default 5)
        
    For operation='fetch':
        - url (str): URL to fetch content from
        
    For operation='ai_search':
        - query (str): Question to ask AI with web search
        
    For operation='weather':
        - location (str): City name, or 'current' for your location
        
    For operation='gps':
        - (no args needed)
        
    Returns:
        JSON string with operation result
    """
    try:
        if operation == 'search':
            query = kwargs.get('query', '')
            num_results = kwargs.get('num_results', 5)
            results = _google_search(query, num_results)
            return json.dumps({"status": "success", "content": results})
        
        elif operation == 'fetch':
            url = kwargs.get('url', '')
            if not url:
                return json.dumps({"status": "error", "content": "URL required"})
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return json.dumps({"status": "success", "content": response.text[:5000]})
        
        elif operation == 'ai_search':
            query = kwargs.get('query', '')
            if not query:
                return json.dumps({"status": "error", "content": "Query required"})
            response = requests.post(
                f"{SERVER_URL}/tools/google_ai",
                json={"query": query},
                timeout=DEFAULT_TIMEOUT
            )
            response.raise_for_status()
            result = response.json()
            if result.get("status") == "success":
                return json.dumps({"status": "success", "content": result.get('content', '')})
            return json.dumps({"status": "error", "content": result.get('content', 'Unknown error')})
        
        elif operation == 'weather':
            location = kwargs.get('location', 'current')
            if location == 'current':
                location = _get_gps_location()
            api_key = os.getenv("WEATHER_API_KEY")
            url = f"http://api.weatherapi.com/v1/current.json?key={api_key}&q={location}&aqi=no"
            response = requests.get(url)
            data = response.json()
            if "error" in data:
                return json.dumps({"status": "error", "content": data["error"]["message"]})
            current = data["current"]
            return json.dumps({
                "status": "success",
                "content": {
                    "location": data["location"]["name"],
                    "temperature": current["temp_c"],
                    "humidity": current["humidity"],
                    "wind_speed": current["wind_kph"],
                    "description": current["condition"]["text"]
                }
            })
        
        elif operation == 'gps':
            return _get_gps_location()
        
        else:
            return json.dumps({"status": "error", "content": f"Unknown operation: {operation}"})
    except Exception as e:
        return json.dumps({"status": "error", "content": str(e)})


def _get_gps_location():
    """Get current GPS location as city name."""
    try:
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
        result = subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-Command", ps_script], capture_output=True, text=True)
        if result.returncode != 0:
            return "Error: " + result.stderr.strip()
        
        output = result.stdout.strip()
        if output == "Unknown location":
            return "Location services unavailable"
        
        lat, lng = map(float, output.split(','))
        geolocator = Nominatim(user_agent="gps_city_locator")
        location = geolocator.reverse((lat, lng), language='en')
        
        if location and 'city' in location.raw['address']:
            return location.raw['address']['city']
        elif location and 'town' in location.raw['address']:
            return location.raw['address']['town']
        elif location and 'village' in location.raw['address']:
            return location.raw['address']['village']
        return "City not found"
    except Exception as e:
        return f"Error: {str(e)}"


# Legacy aliases for backward compatibility
def get_weather(location: str = "current"):
    return web(operation="weather", location=location)

def get_gps_location():
    return web(operation="gps")

def get_google_ai_response(query: str):
    return web(operation="ai_search", query=query)

def fetch_website_data(url: str):
    return web(operation="fetch", url=url)