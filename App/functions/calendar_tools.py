wimport json
import os
import datetime
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.transport.requests import Request

SCOPES = ['https://www.googleapis.com/auth/calendar']

def _get_credentials():
    """Get or refresh Google Calendar credentials."""
    creds = None
    token_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'calendar_token.json')
    client_secrets = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'calendar_client_secrets.json')
    
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(client_secrets):
                return None, "Client secrets file not found. Add 'calendar_client_secrets.json' to data/ folder"
            flow = InstalledAppFlow.from_client_secrets_file(client_secrets, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, 'w') as token:
            token.write(creds.to_json())
    
    return creds, None

def _get_service():
    """Build the Calendar service."""
    creds, error = _get_credentials()
    if error:
        return None, error
    service = build('calendar', 'v3', credentials=creds)
    return service, None


def calendar(operation: str, **kwargs):
    """Google Calendar operations - list, create, or delete events.
    
    Args:
        operation: 'list', 'create', or 'delete'
        
    For operation='list':
        - max_results (int): Max events to return (default 10)
        - days_ahead (int): Days to look ahead (default 7)
        
    For operation='create':
        - title (str): Event title
        - start_datetime (str): Start time in ISO format (e.g., '2024-04-25T14:00:00')
        - duration_minutes (int): Duration in minutes (default 60)
        - description (str): Event description (optional)
        - location (str): Event location (optional)
        
    For operation='delete':
        - event_id (str): ID of event to delete
        
    Returns:
        JSON string with operation result
    """
    service, error = _get_service()
    if error:
        return json.dumps({'status': 'error', 'content': error})
    
    try:
        if operation == 'list':
            return _list_events(service, **kwargs)
        elif operation == 'create':
            return _create_event(service, **kwargs)
        elif operation == 'delete':
            return _delete_event(service, **kwargs)
        else:
            return json.dumps({'status': 'error', 'content': f'Unknown operation: {operation}'})
    except Exception as e:
        return json.dumps({'status': 'error', 'content': str(e)})


def _list_events(service, max_results: int = 10, days_ahead: int = 7):
    """List upcoming calendar events."""
    now = datetime.datetime.utcnow()
    end_time = (now + datetime.timedelta(days=days_ahead)).isoformat() + 'Z'
    now = now.isoformat() + 'Z'
    
    try:
        events_result = service.events().list(
            calendarId='primary',
            timeMin=now,
            timeMax=end_time,
            maxResults=max_results,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        if not events:
            return json.dumps({'status': 'success', 'content': 'No upcoming events found'})
        
        event_list = []
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            event_list.append({
                'id': event['id'],
                'summary': event.get('summary', 'No title'),
                'start': start,
                'end': end,
                'description': event.get('description', ''),
                'location': event.get('location', '')
            })
        
        return json.dumps({'status': 'success', 'content': event_list})
    
    except HttpError as e:
        return json.dumps({'status': 'error', 'content': f'HTTP Error: {e}'})


def _create_event(service, title: str, start_datetime: str, duration_minutes: int = 60, 
                  description: str = "", location: str = ""):
    """Create a new calendar event."""
    try:
        start_dt = datetime.datetime.fromisoformat(start_datetime.replace('Z', '+00:00'))
        end_dt = start_dt + datetime.timedelta(minutes=duration_minutes)
        
        event = {
            'summary': title,
            'description': description,
            'location': location,
            'start': {
                'dateTime': start_dt.isoformat(),
                'timeZone': 'UTC',
            },
            'end': {
                'dateTime': end_dt.isoformat(),
                'timeZone': 'UTC',
            },
        }
        
        created_event = service.events().insert(calendarId='primary', body=event).execute()
        
        return json.dumps({
            'status': 'success',
            'content': {
                'id': created_event['id'],
                'summary': created_event['summary'],
                'start': created_event['start']['dateTime'],
                'end': created_event['end']['dateTime'],
                'link': created_event.get('htmlLink', '')
            }
        })
    
    except ValueError as e:
        return json.dumps({'status': 'error', 'content': f'Invalid datetime format. Use ISO format (e.g., 2024-04-25T14:00:00)'})
    except HttpError as e:
        return json.dumps({'status': 'error', 'content': f'HTTP Error: {e}'})


def _delete_event(service, event_id: str):
    """Delete a calendar event."""
    try:
        service.events().delete(calendarId='primary', eventId=event_id).execute()
        return json.dumps({'status': 'success', 'content': f'Event {event_id} deleted'})
    except HttpError as e:
        return json.dumps({'status': 'error', 'content': f'HTTP Error: {e}'})


if __name__ == "__main__":
    print("Testing calendar...")
    result = calendar('list')
    print(result)