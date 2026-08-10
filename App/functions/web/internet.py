import json
import os
import re
import subprocess
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import requests
from bs4 import BeautifulSoup
from geopy.geocoders import Nominatim

DEFAULT_TIMEOUT = 30
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_settings_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "settings.json"
)
try:
    with open(_settings_path, "r", encoding="utf-8") as _f:
        os.environ.update({k: str(v) for k, v in json.load(_f).items()})
except Exception:
    pass

SERVER_URL = (os.getenv("SERVER_URL") or os.getenv("AVA_SERVER_URL") or "").rstrip("/")


def _resolve_redirect_url(href: str) -> str:
    """Unwrap DuckDuckGo /l/?uddg=… redirect links to the real destination."""
    if not href:
        return ""
    raw = href.strip()
    if raw.startswith("//"):
        raw = "https:" + raw
    try:
        parsed = urlparse(raw)
        qs = parse_qs(parsed.query)
        if "uddg" in qs and qs["uddg"]:
            return unquote(qs["uddg"][0])
        if "u" in qs and qs["u"]:
            return unquote(qs["u"][0])
    except Exception:
        pass
    return raw


def _is_useful_result_url(url: str) -> bool:
    if not url or not url.startswith(("http://", "https://")):
        return False
    host = (urlparse(url).netloc or "").lower()
    if not host:
        return False
    blocked = (
        "duckduckgo.com",
        "google.com",
        "google.co",
        "bing.com",
        "yahoo.com",
        "yandex.",
        "baidu.com",
    )
    return not any(b in host for b in blocked)


def _tavily_search(query: str, num_results: int = 5) -> list[dict[str, Any]]:
    """Primary search via Tavily (same key used by the research agent)."""
    api_key = (os.getenv("TAVILY_API_KEY") or "").strip()
    if not api_key or "paste" in api_key.lower():
        return []
    try:
        from tavily import TavilyClient
    except ImportError:
        return []

    try:
        client = TavilyClient(api_key=api_key)
        raw = client.search(
            query=query,
            max_results=max(1, min(int(num_results), 10)),
            search_depth="basic",
            include_answer=False,
            include_raw_content=False,
        )
        out: list[dict[str, Any]] = []
        for item in raw.get("results") or []:
            url = (item.get("url") or "").strip()
            if not _is_useful_result_url(url):
                continue
            out.append(
                {
                    "title": (item.get("title") or "").strip() or urlparse(url).netloc,
                    "url": url,
                    "snippet": (item.get("content") or "").strip()[:400],
                }
            )
            if len(out) >= num_results:
                break
        return out
    except Exception as exc:
        print(f"[web.search] tavily failed: {exc}")
        return []


def _duckduckgo_html_search(query: str, num_results: int = 5) -> list[dict[str, Any]]:
    """Fallback search via DuckDuckGo HTML results page."""
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    try:
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as exc:
        print(f"[web.search] ddg html failed: {exc}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for result in soup.select("div.result"):
        a = result.select_one("a.result__a")
        if not a:
            continue
        title = a.get_text(" ", strip=True)
        url = _resolve_redirect_url(a.get("href") or "")
        if not _is_useful_result_url(url) or url in seen:
            continue
        snippet_el = result.select_one("a.result__snippet") or result.select_one(
            ".result__snippet"
        )
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        seen.add(url)
        out.append({"title": title or urlparse(url).netloc, "url": url, "snippet": snippet[:400]})
        if len(out) >= num_results:
            break

    # Secondary parse if the page layout differs
    if not out:
        for a in soup.select("a.result__a"):
            title = a.get_text(" ", strip=True)
            url = _resolve_redirect_url(a.get("href") or "")
            if not _is_useful_result_url(url) or url in seen:
                continue
            seen.add(url)
            out.append(
                {
                    "title": title or urlparse(url).netloc,
                    "url": url,
                    "snippet": "",
                }
            )
            if len(out) >= num_results:
                break
    return out


def _googlesearch_fallback(query: str, num_results: int = 5) -> list[dict[str, Any]]:
    """Last-resort URL-only search (often empty due to Google blocking)."""
    try:
        from googlesearch import search
    except ImportError:
        return []
    try:
        urls = list(search(query, num_results=max(1, min(int(num_results), 10))))
        return [
            {"title": urlparse(u).netloc or u, "url": u, "snippet": ""}
            for u in urls
            if _is_useful_result_url(u)
        ][:num_results]
    except Exception as exc:
        print(f"[web.search] googlesearch failed: {exc}")
        return []


def _web_search(query: str, num_results: int = 5) -> dict[str, Any]:
    """
    Search the web and return structured results.

    Provider order:
      1) Tavily (reliable API, already configured for research)
      2) DuckDuckGo HTML scrape
      3) googlesearch-python (often blocked / empty)
    """
    query = (query or "").strip()
    if not query:
        return {"status": "error", "content": "Query required", "results": [], "provider": None}

    try:
        n = max(1, min(int(num_results or 5), 10))
    except (TypeError, ValueError):
        n = 5

    results = _tavily_search(query, n)
    provider = "tavily" if results else None

    if not results:
        results = _duckduckgo_html_search(query, n)
        provider = "duckduckgo" if results else None

    if not results:
        results = _googlesearch_fallback(query, n)
        provider = "googlesearch" if results else None

    if not results:
        return {
            "status": "error",
            "content": "No search results returned (all providers failed or empty).",
            "results": [],
            "provider": None,
            "query": query,
        }

    # Keep a flat URL list for backward compatibility with callers that expect
    # content to be a list of URL strings, while also exposing rich results.
    urls = [r["url"] for r in results if r.get("url")]
    return {
        "status": "success",
        "content": results,  # rich objects: title/url/snippet
        "urls": urls,
        "results": results,
        "provider": provider,
        "query": query,
    }


def _extract_text(html: str) -> str:
    """Extract clean text from HTML content."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        body_content = soup.body
        if body_content:
            clean_text = re.sub(r"<[^>]+>", "", body_content.get_text())
            clean_text = clean_text.replace("\n", " ")
            clean_text = re.sub(r"\s+", " ", clean_text).strip()
            return clean_text
        return ""
    except Exception:
        return ""


def _scrape_urls(links: list) -> list:
    """Scrape text content from a list of URLs."""
    results = []
    for url in links:
        try:
            response = requests.get(
                url, timeout=10, headers={"User-Agent": USER_AGENT}
            )
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
        if operation == "search":
            query = kwargs.get("query", "")
            num_results = kwargs.get("num_results", 5)
            payload = _web_search(query, num_results)
            return json.dumps(payload, ensure_ascii=False)

        elif operation == "fetch":
            url = kwargs.get("url", "")
            if not url:
                return json.dumps({"status": "error", "content": "URL required"})
            response = requests.get(
                url, timeout=10, headers={"User-Agent": USER_AGENT}
            )
            response.raise_for_status()
            return json.dumps(
                {"status": "success", "content": response.text[:5000]},
                ensure_ascii=False,
            )

        elif operation == "ai_search":
            query = kwargs.get("query", "")
            if not query:
                return json.dumps({"status": "error", "content": "Query required"})
            if not SERVER_URL:
                return json.dumps(
                    {
                        "status": "error",
                        "content": "SERVER_URL / AVA_SERVER_URL not configured",
                    }
                )
            response = requests.post(
                f"{SERVER_URL}/tools/google_ai",
                json={"query": query},
                timeout=DEFAULT_TIMEOUT,
            )
            response.raise_for_status()
            result = response.json()
            if result.get("status") == "success":
                return json.dumps(
                    {"status": "success", "content": result.get("content", "")},
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "status": "error",
                    "content": result.get("content", "Unknown error"),
                },
                ensure_ascii=False,
            )

        elif operation == "weather":
            location = kwargs.get("location", "current")
            if location == "current":
                location = _get_gps_location()
            api_key = os.getenv("WEATHER_API_KEY")
            url = (
                f"http://api.weatherapi.com/v1/current.json"
                f"?key={api_key}&q={location}&aqi=no"
            )
            response = requests.get(url, timeout=DEFAULT_TIMEOUT)
            data = response.json()
            if "error" in data:
                return json.dumps(
                    {"status": "error", "content": data["error"]["message"]}
                )
            current = data["current"]
            return json.dumps(
                {
                    "status": "success",
                    "content": {
                        "location": data["location"]["name"],
                        "temperature": current["temp_c"],
                        "humidity": current["humidity"],
                        "wind_speed": current["wind_kph"],
                        "description": current["condition"]["text"],
                    },
                },
                ensure_ascii=False,
            )

        elif operation == "gps":
            return _get_gps_location()

        else:
            return json.dumps(
                {"status": "error", "content": f"Unknown operation: {operation}"}
            )
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
        result = subprocess.run(
            ["powershell", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return "Error: " + result.stderr.strip()

        output = result.stdout.strip()
        if output == "Unknown location":
            return "Location services unavailable"

        lat, lng = map(float, output.split(","))
        geolocator = Nominatim(user_agent="gps_city_locator")
        location = geolocator.reverse((lat, lng), language="en")

        if location and "city" in location.raw["address"]:
            return location.raw["address"]["city"]
        elif location and "town" in location.raw["address"]:
            return location.raw["address"]["town"]
        elif location and "village" in location.raw["address"]:
            return location.raw["address"]["village"]
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


def _google_search(query, num_results=5):
    """Legacy helper — returns URL list only (prefer web(operation='search'))."""
    payload = _web_search(query, num_results)
    if payload.get("status") == "success":
        return payload.get("urls") or [
            r.get("url") for r in (payload.get("results") or []) if r.get("url")
        ]
    return []
