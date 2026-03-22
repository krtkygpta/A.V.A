import pyautogui
import datetime
import os
import cv2
import base64
import json
from groq import Groq
from config import GROQ_API_KEY

# BASE DIRECTORIES
FUNCTIONS_DIR = os.path.dirname(os.path.abspath(__file__))
CLIENT_DIR = os.path.dirname(FUNCTIONS_DIR)
DATA_DIR = os.path.join(CLIENT_DIR, 'data')

# CAPTURE DIRECTORIES
CAPTURED_IMAGES_DIR = os.path.join(DATA_DIR, 'captured_images')
CAPTURED_SCREENS_DIR = os.path.join(DATA_DIR, 'captured_screens')

# Initialize Groq client
client = Groq(api_key=GROQ_API_KEY)

def encode_image(image_path):
    """Encode image to base64 string."""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def capture_and_save_image(camera_index=1, folder=None):
    """Capture an image from a specific camera index and return the file path."""
    if folder is None:
        folder = CAPTURED_IMAGES_DIR

    if not os.path.exists(folder):
        os.makedirs(folder)
    
    camera = cv2.VideoCapture(camera_index)
    
    if not camera.isOpened():
        # If requested index fails, try index 0 as absolute fallback
        if camera_index != 0:
            camera = cv2.VideoCapture(0)
            if not camera.isOpened():
                print(f"Error: Could not open camera at index {camera_index} or 0")
                return None
        else:
            print(f"Error: Could not open camera at index {camera_index}")
            return None

    ret, frame = camera.read()
    camera.release()

    if ret:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        image_name = f"{timestamp}.jpg"
        image_path = os.path.join(folder, image_name)
        cv2.imwrite(image_path, frame)
        return image_path
    else:
        print(f"Error: Could not capture frame from camera {camera_index}")
        return None

def analyze_image_with_groq(image_path, query):
    """Analyze an image using Groq's Vision model."""
    try:
        base64_image = encode_image(image_path)
        
        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": query},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}",
                            },
                        },
                    ],
                }
            ],
            model="meta-llama/llama-4-scout-17b-16e-instruct",
        )
        
        return chat_completion.choices[0].message.content
    except Exception as e:
        return f"Error analyzing image with Groq: {str(e)}"

def capture_screen():
    """Capture a screenshot and return the file path."""
    if not os.path.exists(CAPTURED_SCREENS_DIR):
        os.makedirs(CAPTURED_SCREENS_DIR)
    
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    screen_path = os.path.join(CAPTURED_SCREENS_DIR, f"{timestamp}.jpg")

    try:
        screenshot = pyautogui.screenshot()
        screenshot.save(screen_path)
        return screen_path
    except Exception as e:
        print(f"Error capturing screen: {e}")
        return None

def image_tool(tool, query, camera_index=1):
    """Tool to analyze images from camera or screen."""
    if tool == 'camera':
        path = capture_and_save_image(camera_index=camera_index)
        if path is None:
            return json.dumps({'status': 'error', 'content': f'Failed to capture image from camera {camera_index}'})
        
        response = analyze_image_with_groq(path, query)
        return response
    elif tool == 'screen':
        path = capture_screen()
        if path is None:
            return json.dumps({'status': 'error', 'content': 'Failed to capture screenshot'})
        
        response = analyze_image_with_groq(path, query)
        return response
    else:
        return json.dumps({'status': 'error', 'content': f'Unknown tool: {tool}. Use "camera" or "screen"'})

