# Google Calendar Setup

## Step 1: Enable Google Calendar API

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select existing)
3. Go to **APIs & Services > Library**
4. Search "Google Calendar API" and enable it
5. Go to **APIs & Services > OAuth consent screen**
   - Select **External**
   - Fill in app name (e.g., "AVA Assistant")
   - Add your email as test user
6. Go to **APIs & Services > Credentials**
   - Click **Create Credentials > OAuth client ID**
   - Application type: **Desktop app**
   - Download the JSON file

## Step 2: Configure AVA

Copy the downloaded JSON contents into:
```
App/data/calendar_client_secrets.json
```

Or just replace the placeholder values in the file with:
- `client_id`: from JSON
- `project_id`: from JSON  
- `client_secret`: from JSON

## Step 3: First Run

On first use, AVA will open a browser window for Google login.
After auth, credentials are saved to `App/data/calendar_token.json`.

## Available Functions

| Function | Description |
|----------|-------------|
| `list_calendar_events` | List upcoming events (max_results, days_ahead) |
| `create_calendar_event` | Create event (title, start_datetime, duration_minutes, description, location) |
| `delete_calendar_event` | Delete event by ID |

## Example Usage

```
list_calendar_events(max_results=5, days_ahead=7)
create_calendar_event(title="Meeting", start_datetime="2024-04-25T14:00:00", duration_minutes=60)
delete_calendar_event(event_id="abc123...")
```