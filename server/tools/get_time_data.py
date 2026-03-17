import datetime
import json
def day_suffix(day):
    if 11 <= day <= 13:
        return "th"
    else:
        return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
def get_time_date(args=None):
    current_datetime = datetime.datetime.now()

# Format date as "12th October 2024"
    day_with_suffix = str(current_datetime.day) + day_suffix(current_datetime.day)

# Format date as "12th October 2024" without using %-d
    formatted_date = f"{day_with_suffix} {current_datetime.strftime('%B %Y')}"

    # Format time as "03:45:30 PM" or "03:45 PM"
    formatted_time = current_datetime.strftime("%I:%M %p")
    return json.dumps({
        "status": "success",
        "message": f"time : {formatted_time} and date : {formatted_date}",
        
    })

# print(get_time_date())