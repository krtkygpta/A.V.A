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

## Step 3: First Run

On first use, AVA will open a browser window for Google login.
After auth, credentials are saved to `App/data/calendar_token.json` and automatically refreshed.

## Available Operations

| Operation | Description | Key Parameters |
|-----------|-------------|----------------|
| `list` | List/search events | `query`, `date`, `start_date`, `end_date`, `days_ahead`, `max_results` |
| `create` | Create new event (datetime or all-day) | `title`, `start_datetime`, `end_datetime`, `duration_minutes`, `description`, `location`, `all_day` |
| `update` | Update/reschedule event | `event_id`, `title`, `start_datetime`, `duration_minutes`, `description`, `location` |
| `delete` | Delete event | `event_id` |
| `get` | Get event details | `event_id` |

## Example Usage

```python
# List upcoming events or search by query / date
calendar(operation="list", query="meeting", days_ahead=14)
calendar(operation="list", date="2026-09-07")

# Create a timed event (auto timezone-aware)
calendar(operation="create", title="Team Sync", start_datetime="2026-09-01T14:00:00", duration_minutes=45)

# Create an all-day event
calendar(operation="create", title="Holiday", start_datetime="2026-09-10", all_day=True)

# Update or reschedule
calendar(operation="update", event_id="abc123...", start_datetime="2026-09-01T15:00:00")

# Delete an event
calendar(operation="delete", event_id="abc123...")
```