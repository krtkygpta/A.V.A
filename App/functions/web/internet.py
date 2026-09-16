import datetime
from datetime import timedelta
import json
import os
import re
import subprocess
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlparse

import requests
try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

try:
    from geopy.geocoders import Nominatim
except ImportError:
    Nominatim = None

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


def web(operation: Optional[str] = None, **kwargs):
    """Unified web operations: search, fetch URLs, AI search, weather, GPS.
    Auto-detects operation if not explicitly provided.
    """
    op = (operation or kwargs.get("operation") or "").strip().lower()
    if not op:
        if kwargs.get("url"):
            op = "fetch"
        elif kwargs.get("location") and ("weather" in str(kwargs).lower() or kwargs.get("days") or kwargs.get("hour")):
            op = "weather"
        elif kwargs.get("mode") == "research" or kwargs.get("ai_search"):
            op = "ai_search"
        else:
            op = "search"

    operation = op
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
            if SERVER_URL:
                try:
                    response = requests.post(
                        f"{SERVER_URL}/tools/google_ai",
                        json={"query": query},
                        timeout=min(DEFAULT_TIMEOUT, 8),
                    )
                    if response.ok:
                        result = response.json()
                        if result.get("status") == "success" and result.get("content"):
                            return json.dumps(
                                {"status": "success", "content": result.get("content", "")},
                                ensure_ascii=False,
                            )
                except Exception:
                    pass

            # Graceful fallback to search results
            payload = _web_search(query, kwargs.get("num_results", 5))
            return json.dumps(payload, ensure_ascii=False)

        elif operation == "weather":
            location = kwargs.get("location", "current")
            date = kwargs.get("date")
            days = kwargs.get("days")
            hour = kwargs.get("hour")
            start_hour = kwargs.get("start_hour")
            end_hour = kwargs.get("end_hour")
            start_time = kwargs.get("start_time")
            end_time = kwargs.get("end_time")
            start_date = kwargs.get("start_date")
            end_date = kwargs.get("end_date")
            time_range = kwargs.get("time_range")
            hour_range = kwargs.get("hour_range")
            include_hourly = kwargs.get("include_hourly", False)
            result = _fetch_weather_report(
                location=location,
                date=date,
                days=days,
                hour=hour,
                start_hour=start_hour,
                end_hour=end_hour,
                start_time=start_time,
                end_time=end_time,
                start_date=start_date,
                end_date=end_date,
                time_range=time_range,
                hour_range=hour_range,
                include_hourly=include_hourly,
            )
            return json.dumps(result, ensure_ascii=False)

        elif operation == "gps":
            return _get_gps_location()

        else:
            return json.dumps(
                {"status": "error", "content": f"Unknown operation: {operation}"}
            )
    except Exception as e:
        return json.dumps({"status": "error", "content": str(e)})


def _parse_weather_date(date_val: Any) -> str | None:
    """Parse relative or formatted date string into YYYY-MM-DD format."""
    if not date_val:
        return None
    s = str(date_val).strip().lower()
    if not s:
        return None
    today = datetime.date.today()
    if s in ("today", "now", "current", "present"):
        return today.isoformat()
    if s == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    if s in ("day after tomorrow", "overmorrow"):
        return (today + timedelta(days=2)).isoformat()
    if s == "yesterday":
        return (today - timedelta(days=1)).isoformat()

    # in X days / +X days
    m_in = re.match(r"^(?:in\s+)?(\d+)\s*(?:days?|d)$", s) or re.match(
        r"^\+(\d+)\s*(?:days?|d)?$", s
    )
    if m_in:
        return (today + timedelta(days=int(m_in.group(1)))).isoformat()

    # X days ago / -X days
    m_ago = re.match(r"^(\d+)\s*(?:days?|d)\s+ago$", s) or re.match(
        r"^-(\d+)\s*(?:days?|d)?$", s
    )
    if m_ago:
        return (today - timedelta(days=int(m_ago.group(1)))).isoformat()

    # Weekday parsing (e.g. 'monday', 'next friday')
    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    is_next = "next" in s
    for name, wd in weekdays.items():
        if name in s:
            ahead = (wd - today.weekday()) % 7
            if ahead == 0 or is_next:
                ahead += 7
            return (today + timedelta(days=ahead)).isoformat()

    # ISO date YYYY-MM-DD
    m_iso = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m_iso:
        try:
            y, m, d = int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))
            return datetime.date(y, m, d).isoformat()
        except ValueError:
            return "invalid"

    # Common alternate formats: DD-MM-YYYY or MM/DD/YYYY
    m_alt = re.match(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$", s)
    if m_alt:
        p1, p2, y = int(m_alt.group(1)), int(m_alt.group(2)), int(m_alt.group(3))
        try:
            return datetime.date(y, p2, p1).isoformat()
        except ValueError:
            try:
                return datetime.date(y, p1, p2).isoformat()
            except ValueError:
                return "invalid"

    return "invalid"


def _parse_weather_hour(hour_val: Any) -> int | None:
    """Parse hour input (integer, AM/PM string, or 24-hr time) into 0-23 integer."""
    if hour_val is None:
        return None
    if isinstance(hour_val, (int, float)):
        h = int(hour_val)
        return h if 0 <= h <= 23 else None
    s = str(hour_val).strip().lower()
    if not s:
        return None
    if s.isdigit():
        h = int(s)
        return h if 0 <= h <= 23 else None
    m_ampm = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)$", s)
    if m_ampm:
        h = int(m_ampm.group(1))
        meridiem = m_ampm.group(3)
        if 1 <= h <= 12:
            if meridiem == "pm" and h < 12:
                h += 12
            elif meridiem == "am" and h == 12:
                h = 0
            return h if 0 <= h <= 23 else None
        return None
    m_24 = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if m_24:
        h = int(m_24.group(1))
        return h if 0 <= h <= 23 else None
    return None


def _fetch_weather_report(
    location: str = "current",
    date: Any = None,
    days: Any = None,
    hour: Any = None,
    start_hour: Any = None,
    end_hour: Any = None,
    start_time: Any = None,
    end_time: Any = None,
    start_date: Any = None,
    end_date: Any = None,
    time_range: Any = None,
    hour_range: Any = None,
    include_hourly: bool = False,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Fetch current, forecast, specific-date, or hourly weather from WeatherAPI with 14-day limit, date/hour ranges, and edge case validation."""
    if not location or str(location).strip().lower() in (
        "current",
        "here",
        "my location",
        "default",
    ):
        gps_loc = _get_gps_location()
        if (
            gps_loc
            and not gps_loc.startswith("Error")
            and gps_loc != "City not found"
            and "unavailable" not in gps_loc.lower()
        ):
            location = gps_loc
        else:
            location = "London"

    api_key = api_key or os.getenv("WEATHER_API_KEY")
    if not api_key:
        return {
            "status": "error",
            "content": "WEATHER_API_KEY is not configured in settings.json or environment",
        }

    today = datetime.date.today()
    max_forecast_date = today + timedelta(days=14)

    # Resolve date range (start_date / end_date or date)
    primary_date_str = start_date or date
    target_date = None
    if primary_date_str is not None and str(primary_date_str).strip():
        parsed_res = _parse_weather_date(primary_date_str)
        if parsed_res is None or parsed_res == "invalid":
            return {
                "status": "error",
                "content": f"Invalid date format: '{primary_date_str}'. Please provide a valid date like 'today', 'tomorrow', 'in 3 days', or 'YYYY-MM-DD'.",
            }
        target_date = parsed_res

        try:
            date_obj = datetime.date.fromisoformat(target_date)
            if date_obj > max_forecast_date:
                return {
                    "status": "error",
                    "content": f"Weather forecast is limited to a maximum of 14 days in advance (up to {max_forecast_date.isoformat()}). 14 days is the maximum supported range.",
                }
        except ValueError:
            pass

    target_end_date = None
    if end_date is not None and str(end_date).strip():
        parsed_end_res = _parse_weather_date(end_date)
        if parsed_end_res is None or parsed_end_res == "invalid":
            return {
                "status": "error",
                "content": f"Invalid end_date format: '{end_date}'.",
            }
        target_end_date = parsed_end_res
        try:
            end_date_obj = datetime.date.fromisoformat(target_end_date)
            if end_date_obj > max_forecast_date:
                return {
                    "status": "error",
                    "content": f"Weather forecast is limited to a maximum of 14 days in advance (up to {max_forecast_date.isoformat()}). 14 days is the maximum supported range.",
                }
        except ValueError:
            pass

    # Resolve hour range (hour_range, time_range, start_hour/end_hour, or hour)
    range_str = hour_range or time_range
    if range_str and isinstance(range_str, str):
        parts = re.split(r"\s*(?:-|to)\s*", range_str.strip())
        if len(parts) == 2:
            start_hour = start_hour or parts[0]
            end_hour = end_hour or parts[1]

    filter_start_dt = None
    filter_end_dt = None
    if start_time and isinstance(start_time, str):
        st_clean = start_time.strip()
        if " " in st_clean:
            filter_start_dt = st_clean
        else:
            start_hour = start_hour or st_clean
    if end_time and isinstance(end_time, str):
        et_clean = end_time.strip()
        if " " in et_clean:
            filter_end_dt = et_clean
        else:
            end_hour = end_hour or et_clean

    target_start_hour = None
    if start_hour is not None and str(start_hour).strip():
        parsed_sh = _parse_weather_hour(start_hour)
        if parsed_sh is None:
            return {
                "status": "error",
                "content": f"Invalid start_hour: '{start_hour}'. Must be between 0 and 23 (e.g. 14, '2pm', '14:00').",
            }
        target_start_hour = parsed_sh

    target_end_hour = None
    if end_hour is not None and str(end_hour).strip():
        parsed_eh = _parse_weather_hour(end_hour)
        if parsed_eh is None:
            return {
                "status": "error",
                "content": f"Invalid end_hour: '{end_hour}'. Must be between 0 and 23 (e.g. 18, '6pm', '18:00').",
            }
        target_end_hour = parsed_eh

    # Single hour requested
    if hour is not None and str(hour).strip() and target_start_hour is None:
        parsed_h = _parse_weather_hour(hour)
        if parsed_h is None:
            return {
                "status": "error",
                "content": f"Invalid hour: '{hour}'. Please provide an hour between 0 and 23 (e.g. 14, '2pm', '14:00').",
            }
        target_start_hour = parsed_h
        if target_end_hour is None:
            target_end_hour = parsed_h

    # Validate days parameter (14-day max limit)
    num_days = None
    if days is not None and str(days).strip():
        try:
            val = int(days)
            if val > 14:
                return {
                    "status": "error",
                    "content": "Weather forecast is limited to a maximum of 14 days in advance. 14 days is the maximum supported range.",
                }
            if val < 1:
                return {
                    "status": "error",
                    "content": "Days parameter must be a positive integer between 1 and 14.",
                }
            num_days = val
        except (ValueError, TypeError):
            return {
                "status": "error",
                "content": f"Invalid days parameter: '{days}'. Must be an integer between 1 and 14.",
            }

    # If end_date was provided, ensure num_days spans until end_date
    if target_end_date:
        try:
            end_d_obj = datetime.date.fromisoformat(target_end_date)
            delta = (end_d_obj - today).days + 1
            num_days = max(num_days or 1, min(14, max(1, delta)))
        except ValueError:
            pass

    # Default to current weather if no date or days mentioned
    if not target_date and num_days is None:
        num_days = 1

    today_str = today.isoformat()
    is_past = bool(target_date and target_date < today_str)

    try:
        if is_past:
            url = (
                f"http://api.weatherapi.com/v1/history.json"
                f"?key={api_key}&q={location}&dt={target_date}"
            )
            if target_start_hour is not None and target_start_hour == target_end_hour:
                url += f"&hour={target_start_hour}"
        else:
            url = (
                f"http://api.weatherapi.com/v1/forecast.json"
                f"?key={api_key}&q={location}&days={num_days or 3}&aqi=yes&alerts=yes"
            )
            if target_date and not target_end_date:
                url += f"&dt={target_date}"
            if target_start_hour is not None and target_start_hour == target_end_hour:
                url += f"&hour={target_start_hour}"

        response = requests.get(url, timeout=DEFAULT_TIMEOUT)
        data = response.json()
        if "error" in data:
            return {
                "status": "error",
                "content": data["error"].get("message", "Weather API request failed"),
            }

        loc = data.get("location", {})
        curr = data.get("current", {})
        forecast_days = data.get("forecast", {}).get("forecastday", [])
        alerts_raw = data.get("alerts", {}).get("alert", [])

        content: dict[str, Any] = {
            "location": loc.get("name") or str(location),
            "region": loc.get("region"),
            "country": loc.get("country"),
            "localtime": loc.get("localtime"),
            # Backward compatibility fields
            "temperature": (
                curr.get("temp_c")
                if curr
                else (forecast_days[0]["day"]["avgtemp_c"] if forecast_days else None)
            ),
            "humidity": (
                curr.get("humidity")
                if curr
                else (forecast_days[0]["day"]["avghumidity"] if forecast_days else None)
            ),
            "wind_speed": (
                curr.get("wind_kph")
                if curr
                else (forecast_days[0]["day"]["maxwind_kph"] if forecast_days else None)
            ),
            "description": (
                curr.get("condition", {}).get("text")
                if curr
                else (
                    forecast_days[0]["day"]["condition"]["text"]
                    if forecast_days
                    else ""
                )
            ),
        }

        if curr:
            content["current"] = {
                "temp_c": curr.get("temp_c"),
                "temp_f": curr.get("temp_f"),
                "feelslike_c": curr.get("feelslike_c"),
                "feelslike_f": curr.get("feelslike_f"),
                "condition": curr.get("condition", {}).get("text"),
                "humidity": curr.get("humidity"),
                "wind_kph": curr.get("wind_kph"),
                "wind_mph": curr.get("wind_mph"),
                "wind_dir": curr.get("wind_dir"),
                "chance_of_rain": curr.get(
                    "chance_of_rain", curr.get("will_it_rain")
                ),
                "precip_mm": curr.get("precip_mm"),
                "uv": curr.get("uv"),
                "cloud": curr.get("cloud"),
                "visibility_km": curr.get("vis_km"),
            }
            if "air_quality" in curr and curr["air_quality"]:
                content["current"]["air_quality_us_epa"] = curr[
                    "air_quality"
                ].get("us-epa-index")

        hourly_range_list = []
        if forecast_days:
            forecast_list = []
            for fd in forecast_days:
                fd_date = fd.get("date")
                # If date range filtering active, only include matching days
                if target_date and target_end_date:
                    if not (target_date <= fd_date <= target_end_date):
                        continue

                d_info = fd.get("day", {})
                astro = fd.get("astro", {})
                day_obj: dict[str, Any] = {
                    "date": fd_date,
                    "max_temp_c": d_info.get("maxtemp_c"),
                    "min_temp_c": d_info.get("mintemp_c"),
                    "avg_temp_c": d_info.get("avgtemp_c"),
                    "condition": d_info.get("condition", {}).get("text"),
                    "chance_of_rain": d_info.get("daily_chance_of_rain"),
                    "chance_of_snow": d_info.get("daily_chance_of_snow"),
                    "precip_mm": d_info.get("totalprecip_mm"),
                    "max_wind_kph": d_info.get("maxwind_kph"),
                    "uv": d_info.get("uv"),
                    "sunrise": astro.get("sunrise"),
                    "sunset": astro.get("sunset"),
                }

                # Process hourly data for this day
                for h in fd.get("hour", []):
                    h_time_str = h.get("time", "")
                    h_num = None
                    if " " in h_time_str:
                        try:
                            h_num = int(h_time_str.split(" ")[1].split(":")[0])
                        except (ValueError, IndexError):
                            pass
                    elif ":" in h_time_str:
                        try:
                            h_num = int(h_time_str.split(":")[0])
                        except (ValueError, IndexError):
                            pass

                    # Check timestamp bounds
                    if filter_start_dt and h_time_str < filter_start_dt:
                        continue
                    if filter_end_dt and h_time_str > filter_end_dt:
                        continue

                    # Check hour bounds
                    if target_start_hour is not None and target_end_hour is not None:
                        if not (target_start_hour <= h_num <= target_end_hour):
                            continue
                    elif target_start_hour is not None:
                        if h_num != target_start_hour:
                            continue

                    hourly_item = {
                        "time": h_time_str,
                        "temp_c": h.get("temp_c"),
                        "feelslike_c": h.get("feelslike_c"),
                        "condition": h.get("condition", {}).get("text"),
                        "chance_of_rain": h.get("chance_of_rain"),
                        "precip_mm": h.get("precip_mm"),
                        "wind_kph": h.get("wind_kph"),
                        "wind_dir": h.get("wind_dir"),
                        "humidity": h.get("humidity"),
                        "uv": h.get("uv"),
                    }
                    hourly_range_list.append(hourly_item)
                    if (
                        target_start_hour is not None
                        and target_start_hour == target_end_hour
                        and "hourly_focus" not in day_obj
                    ):
                        day_obj["hourly_focus"] = hourly_item

                forecast_list.append(day_obj)
            content["forecast"] = forecast_list

        # Attach hourly results
        if hourly_range_list:
            if (
                target_start_hour is not None
                and target_start_hour == target_end_hour
                and len(hourly_range_list) == 1
            ):
                content["hourly_focus"] = hourly_range_list[0]
            else:
                content["hourly_range"] = hourly_range_list
        elif include_hourly and forecast_days:
            # Flatten all hours if include_hourly requested without filter
            all_hours = []
            for fd in forecast_days:
                for h in fd.get("hour", []):
                    all_hours.append({
                        "time": h.get("time"),
                        "temp_c": h.get("temp_c"),
                        "feelslike_c": h.get("feelslike_c"),
                        "condition": h.get("condition", {}).get("text"),
                        "chance_of_rain": h.get("chance_of_rain"),
                        "wind_kph": h.get("wind_kph"),
                        "humidity": h.get("humidity"),
                    })
            content["hourly"] = all_hours

        if target_date:
            content["target_date"] = target_date
        if target_end_date:
            content["target_end_date"] = target_end_date
        if target_start_hour is not None:
            content["start_hour"] = target_start_hour
        if target_end_hour is not None:
            content["end_hour"] = target_end_hour
        if alerts_raw:
            content["alerts"] = [
                {
                    "headline": a.get("headline"),
                    "severity": a.get("severity"),
                    "urgency": a.get("urgency"),
                    "event": a.get("event"),
                    "desc": a.get("desc"),
                }
                for a in alerts_raw
            ]

        return {"status": "success", "content": content}
    except Exception as exc:
        return {"status": "error", "content": str(exc)}


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
        if not Nominatim:
            return "City not found"
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
def get_weather(
    location: str = "current",
    date: Any = None,
    days: Any = None,
    hour: Any = None,
):
    return web(operation="weather", location=location, date=date, days=days, hour=hour)


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
