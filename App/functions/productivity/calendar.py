import datetime
import json
import os
from typing import Optional, Tuple
import zoneinfo

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/calendar"]

_TIMEZONE_CACHE = {}


def _get_credentials():
    """Get or refresh Google Calendar credentials with graceful error recovery."""
    creds = None
    token_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "data", "calendar_token.json"
    )
    client_secrets = os.path.join(
        os.path.dirname(__file__), "..", "..", "data", "calendar_client_secrets.json"
    )

    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        except Exception:
            creds = None

    if not creds or not creds.valid:
        refresh_success = False
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                refresh_success = True
            except RefreshError:
                refresh_success = False
            except Exception as e:
                return None, f"Failed to refresh Google Calendar token: {e}"

        if not refresh_success:
            if not os.path.exists(client_secrets):
                return (
                    None,
                    "Client secrets file not found. Add 'calendar_client_secrets.json' to data/ folder",
                )
            try:
                flow = InstalledAppFlow.from_client_secrets_file(client_secrets, SCOPES)
                creds = flow.run_local_server(port=0)
            except Exception as e:
                return None, f"OAuth authentication flow failed: {e}"

        try:
            with open(token_path, "w") as token:
                token.write(creds.to_json())
        except Exception:
            pass

    return creds, None


def _get_service():
    """Build the Calendar service."""
    creds, error = _get_credentials()
    if error:
        return None, error
    try:
        service = build("calendar", "v3", credentials=creds)
        return service, None
    except Exception as e:
        return None, f"Failed to build Google Calendar service: {e}"


def _get_calendar_timezone(service, calendar_id: str = "primary") -> str:
    """Retrieve the primary calendar timezone with caching and fallbacks."""
    if calendar_id in _TIMEZONE_CACHE:
        return _TIMEZONE_CACHE[calendar_id]

    try:
        cal = service.calendars().get(calendarId=calendar_id).execute()
        tz_str = cal.get("timeZone")
        if tz_str:
            _TIMEZONE_CACHE[calendar_id] = tz_str
            return tz_str
    except Exception:
        pass

    try:
        local_tz = datetime.datetime.now().astimezone().tzinfo
        if local_tz:
            tz_str = getattr(local_tz, "key", None) or str(local_tz)
            if tz_str:
                return tz_str
    except Exception:
        pass

    return "UTC"


def _parse_time_str(time_str: str, tz_name: str) -> Tuple[bool, datetime.datetime, Optional[datetime.date]]:
    """Parse time string into either a datetime (with tzinfo) or date-only.

    Returns:
        (is_date_only, datetime_obj, date_obj)
    """
    clean_str = str(time_str).strip().replace("Z", "+00:00")

    # Check if date-only format (e.g., '2026-09-07')
    if len(clean_str) == 10 and clean_str.count("-") == 2 and "T" not in clean_str and " " not in clean_str:
        try:
            d = datetime.date.fromisoformat(clean_str)
            try:
                tz = zoneinfo.ZoneInfo(tz_name)
            except Exception:
                tz = datetime.timezone.utc
            dt = datetime.datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=tz)
            return True, dt, d
        except Exception:
            pass

    # Handle space instead of T (e.g. '2026-09-01 10:00:00')
    if " " in clean_str and "T" not in clean_str:
        clean_str = clean_str.replace(" ", "T")

    dt = datetime.datetime.fromisoformat(clean_str)
    if dt.tzinfo is None:
        try:
            tz = zoneinfo.ZoneInfo(tz_name)
            dt = dt.replace(tzinfo=tz)
        except Exception:
            pass
    return False, dt, dt.date()


def calendar(operation: str = "list", **kwargs):
    """Google Calendar operations - list, create, update, delete, or get events.

    Args:
        operation: 'list', 'create', 'update', 'delete', or 'get'
        **kwargs: Operation-specific keyword arguments
    """
    service, error = _get_service()
    if error:
        return json.dumps({"status": "error", "content": error})

    op = str(operation or kwargs.get("action") or "list").strip().lower()

    try:
        if op in ("list", "search", "find", "get_events", "view"):
            return _list_events(service, **kwargs)
        elif op in ("create", "add", "new", "insert", "schedule"):
            return _create_event(service, **kwargs)
        elif op in ("update", "edit", "patch", "modify", "reschedule"):
            return _update_event(service, **kwargs)
        elif op in ("delete", "remove", "cancel"):
            return _delete_event(service, **kwargs)
        elif op in ("get", "read", "details"):
            return _get_event_details(service, **kwargs)
        else:
            return json.dumps(
                {
                    "status": "error",
                    "content": f"Unknown operation: '{operation}'. Supported: list, create, update, delete, get",
                }
            )
    except Exception as e:
        return json.dumps({"status": "error", "content": str(e)})


def _list_events(service, **kwargs):
    """List or search calendar events with timezone awareness, keyword search, and date filtering."""
    calendar_id = str(kwargs.get("calendar_id") or "primary")
    cal_tz = _get_calendar_timezone(service, calendar_id)

    try:
        tz = zoneinfo.ZoneInfo(cal_tz)
    except Exception:
        tz = datetime.timezone.utc

    now = datetime.datetime.now(tz)

    max_results = int(kwargs.get("max_results") or kwargs.get("limit") or kwargs.get("count") or 10)
    days_ahead = int(kwargs.get("days_ahead") or kwargs.get("days") or 7)
    query = str(kwargs.get("query") or kwargs.get("q") or kwargs.get("search") or kwargs.get("keyword") or "").strip()
    include_past = bool(kwargs.get("include_past", False))

    start_raw = (
        kwargs.get("start_date")
        or kwargs.get("start_datetime")
        or kwargs.get("time_min")
        or kwargs.get("date")
        or kwargs.get("from_date")
    )
    end_raw = (
        kwargs.get("end_date")
        or kwargs.get("end_datetime")
        or kwargs.get("time_max")
        or kwargs.get("to_date")
    )

    time_min = None
    time_max = None
    start_dt = None

    if start_raw:
        try:
            is_date_only, start_dt, _ = _parse_time_str(str(start_raw), cal_tz)
            time_min = start_dt.isoformat()
            if is_date_only and not end_raw and "days_ahead" not in kwargs and "days" not in kwargs:
                # If a specific date was passed (e.g. date="2026-09-07"), query that entire 24h day
                end_dt = start_dt + datetime.timedelta(days=1)
                time_max = end_dt.isoformat()
        except Exception:
            time_min = now.isoformat()
    elif include_past:
        time_min = (now - datetime.timedelta(days=days_ahead or 30)).isoformat()
    elif query:
        # Default search query window: past 7 days to next 60 days if unspecified
        time_min = (now - datetime.timedelta(days=7)).isoformat()
    else:
        time_min = now.isoformat()

    if end_raw:
        try:
            is_date_only, end_dt, _ = _parse_time_str(str(end_raw), cal_tz)
            if is_date_only:
                end_dt = end_dt + datetime.timedelta(days=1)
            time_max = end_dt.isoformat()
        except Exception:
            pass

    if not time_max:
        base_start = start_dt if start_dt else now
        if query and not start_raw and "days_ahead" not in kwargs and "days" not in kwargs:
            time_max = (now + datetime.timedelta(days=60)).isoformat()
        else:
            time_max = (base_start + datetime.timedelta(days=days_ahead)).isoformat()

    list_params = {
        "calendarId": calendar_id,
        "timeMin": time_min,
        "timeMax": time_max,
        "maxResults": max_results,
        "singleEvents": True,
        "orderBy": "startTime",
    }
    if query:
        list_params["q"] = query

    try:
        events_result = service.events().list(**list_params).execute()
        events = events_result.get("items", [])
        if not events:
            return json.dumps(
                {"status": "success", "content": "No upcoming events found"}
            )

        event_list = []
        for event in events:
            start = event["start"].get("dateTime", event["start"].get("date"))
            end = event["end"].get("dateTime", event["end"].get("date"))
            event_list.append(
                {
                    "id": event["id"],
                    "summary": event.get("summary", "No title"),
                    "start": start,
                    "end": end,
                    "description": event.get("description", ""),
                    "location": event.get("location", ""),
                    "status": event.get("status", "confirmed"),
                    "link": event.get("htmlLink", ""),
                }
            )

        return json.dumps({"status": "success", "content": event_list})

    except HttpError as e:
        return json.dumps({"status": "error", "content": f"HTTP Error: {e}"})


def _create_event(service, **kwargs):
    """Create a new calendar event with accurate timezone handling and all-day support."""
    calendar_id = str(kwargs.get("calendar_id") or "primary")
    cal_tz = str(kwargs.get("timezone") or _get_calendar_timezone(service, calendar_id))

    title = str(
        kwargs.get("title")
        or kwargs.get("summary")
        or kwargs.get("name")
        or "New Event"
    ).strip()
    description = str(kwargs.get("description") or kwargs.get("desc") or "").strip()
    location = str(kwargs.get("location") or kwargs.get("loc") or "").strip()

    start_raw = (
        kwargs.get("start_datetime")
        or kwargs.get("start_time")
        or kwargs.get("start")
        or kwargs.get("date")
    )
    if not start_raw:
        return json.dumps(
            {
                "status": "error",
                "content": "Missing 'start_datetime' (e.g. '2026-09-01T10:00:00' or '2026-09-01')",
            }
        )

    end_raw = kwargs.get("end_datetime") or kwargs.get("end_time") or kwargs.get("end")
    duration_minutes = int(kwargs.get("duration_minutes") or kwargs.get("duration") or 60)
    force_all_day = bool(kwargs.get("all_day") or kwargs.get("is_all_day") or False)

    try:
        is_date_only, start_dt, start_d = _parse_time_str(str(start_raw), cal_tz)

        if is_date_only or force_all_day:
            start_date_str = start_d.isoformat()
            if end_raw:
                _, _, end_d = _parse_time_str(str(end_raw), cal_tz)
                end_date_str = end_d.isoformat()
            else:
                end_date_str = (start_d + datetime.timedelta(days=1)).isoformat()

            event = {
                "summary": title,
                "description": description,
                "location": location,
                "start": {"date": start_date_str},
                "end": {"date": end_date_str},
            }
        else:
            if end_raw:
                _, end_dt, _ = _parse_time_str(str(end_raw), cal_tz)
            else:
                end_dt = start_dt + datetime.timedelta(minutes=duration_minutes)

            event = {
                "summary": title,
                "description": description,
                "location": location,
                "start": {
                    "dateTime": start_dt.isoformat(),
                    "timeZone": cal_tz,
                },
                "end": {
                    "dateTime": end_dt.isoformat(),
                    "timeZone": cal_tz,
                },
            }

        created_event = (
            service.events().insert(calendarId=calendar_id, body=event).execute()
        )

        start_out = created_event["start"].get(
            "dateTime", created_event["start"].get("date")
        )
        end_out = created_event["end"].get(
            "dateTime", created_event["end"].get("date")
        )

        return json.dumps(
            {
                "status": "success",
                "content": {
                    "id": created_event["id"],
                    "summary": created_event.get("summary", title),
                    "start": start_out,
                    "end": end_out,
                    "timeZone": cal_tz,
                    "link": created_event.get("htmlLink", ""),
                },
            }
        )

    except ValueError as e:
        return json.dumps(
            {
                "status": "error",
                "content": f"Invalid datetime format: {e}. Use ISO format (e.g., '2026-09-01T14:00:00' or '2026-09-01')",
            }
        )
    except HttpError as e:
        return json.dumps({"status": "error", "content": f"HTTP Error: {e}"})


def _update_event(service, **kwargs):
    """Update or reschedule an existing calendar event."""
    calendar_id = str(kwargs.get("calendar_id") or "primary")
    event_id = kwargs.get("event_id") or kwargs.get("id")
    if not event_id:
        return json.dumps(
            {"status": "error", "content": "Missing 'event_id' of event to update"}
        )

    cal_tz = str(kwargs.get("timezone") or _get_calendar_timezone(service, calendar_id))

    try:
        event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()

        if "title" in kwargs or "summary" in kwargs or "name" in kwargs:
            event["summary"] = str(
                kwargs.get("title") or kwargs.get("summary") or kwargs.get("name")
            )
        if "description" in kwargs or "desc" in kwargs:
            event["description"] = str(kwargs.get("description") or kwargs.get("desc"))
        if "location" in kwargs or "loc" in kwargs:
            event["location"] = str(kwargs.get("location") or kwargs.get("loc"))

        start_raw = (
            kwargs.get("start_datetime")
            or kwargs.get("start_time")
            or kwargs.get("start")
            or kwargs.get("date")
        )
        end_raw = kwargs.get("end_datetime") or kwargs.get("end_time") or kwargs.get("end")
        duration_minutes = kwargs.get("duration_minutes") or kwargs.get("duration")

        if start_raw:
            is_date_only, start_dt, start_d = _parse_time_str(str(start_raw), cal_tz)
            if is_date_only:
                start_date_str = start_d.isoformat()
                if end_raw:
                    _, _, end_d = _parse_time_str(str(end_raw), cal_tz)
                    end_date_str = end_d.isoformat()
                else:
                    end_date_str = (start_d + datetime.timedelta(days=1)).isoformat()
                event["start"] = {"date": start_date_str}
                event["end"] = {"date": end_date_str}
            else:
                if end_raw:
                    _, end_dt, _ = _parse_time_str(str(end_raw), cal_tz)
                else:
                    dur = int(duration_minutes or 60)
                    end_dt = start_dt + datetime.timedelta(minutes=dur)
                event["start"] = {"dateTime": start_dt.isoformat(), "timeZone": cal_tz}
                event["end"] = {"dateTime": end_dt.isoformat(), "timeZone": cal_tz}

        updated_event = (
            service.events()
            .update(calendarId=calendar_id, eventId=event_id, body=event)
            .execute()
        )
        start_out = updated_event["start"].get(
            "dateTime", updated_event["start"].get("date")
        )
        end_out = updated_event["end"].get(
            "dateTime", updated_event["end"].get("date")
        )

        return json.dumps(
            {
                "status": "success",
                "content": {
                    "id": updated_event["id"],
                    "summary": updated_event.get("summary", ""),
                    "start": start_out,
                    "end": end_out,
                    "link": updated_event.get("htmlLink", ""),
                },
            }
        )
    except HttpError as e:
        return json.dumps({"status": "error", "content": f"HTTP Error: {e}"})


def _delete_event(service, **kwargs):
    """Delete a calendar event."""
    calendar_id = str(kwargs.get("calendar_id") or "primary")
    event_id = kwargs.get("event_id") or kwargs.get("id")
    if not event_id:
        return json.dumps(
            {"status": "error", "content": "Missing 'event_id' of event to delete"}
        )

    try:
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        return json.dumps(
            {"status": "success", "content": f"Event '{event_id}' deleted successfully"}
        )
    except HttpError as e:
        return json.dumps({"status": "error", "content": f"HTTP Error: {e}"})


def _get_event_details(service, **kwargs):
    """Retrieve details for a single calendar event."""
    calendar_id = str(kwargs.get("calendar_id") or "primary")
    event_id = kwargs.get("event_id") or kwargs.get("id")
    if not event_id:
        return json.dumps({"status": "error", "content": "Missing 'event_id'"})

    try:
        event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        start = event["start"].get("dateTime", event["start"].get("date"))
        end = event["end"].get("dateTime", event["end"].get("date"))
        return json.dumps(
            {
                "status": "success",
                "content": {
                    "id": event["id"],
                    "summary": event.get("summary", "No title"),
                    "start": start,
                    "end": end,
                    "description": event.get("description", ""),
                    "location": event.get("location", ""),
                    "status": event.get("status", "confirmed"),
                    "link": event.get("htmlLink", ""),
                },
            }
        )
    except HttpError as e:
        return json.dumps({"status": "error", "content": f"HTTP Error: {e}"})


if __name__ == "__main__":
    print("Testing calendar...")
    result = calendar("list")
    print(result)
