import pyautogui
import datetime
import os
import cv2
import base64
import json
import requests

SERVER_URL = os.getenv("AVA_SERVER_URL", "http://127.0.0.1:8765").rstrip("/")
DEFAULT_TIMEOUT = float(os.getenv("AVA_SERVER_TIMEOUT", "4"))

# BASE DIRECTORIES
FUNCTIONS_DIR = os.path.dirname(os.path.abspath(__file__))
CLIENT_DIR = os.path.dirname(FUNCTIONS_DIR)
DATA_DIR = os.path.join(CLIENT_DIR, 'data')

# CAPTURE DIRECTORIES
CAPTURED_IMAGES_DIR = os.path.join(DATA_DIR, 'captured_images')
CAPTURED_SCREENS_DIR = os.path.join(DATA_DIR, 'captured_screens')

# Initialize Groq client (kept for backwards compatibility, but image_tool now uses server API)
client = None

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
                print(f"[ImageTool] Camera open error at index {camera_index} or 0")
                return None
        else:
            print(f"[ImageTool] Camera open error at index {camera_index}")
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
        print(f"[ImageTool] Frame capture error from camera {camera_index}")
        return None

def analyze_image_with_groq(image_path, query):
    """Analyze an image via the server API using Groq Vision."""
    try:
        base64_image = encode_image(image_path)
        response = requests.post(
            f"{SERVER_URL}/tools/image_analysis",
            json={"image_base64": base64_image, "query": query},
            timeout=max(DEFAULT_TIMEOUT, 60.0)
        )
        response.raise_for_status()
        result = response.json()
        if result.get("status") == "success":
            return result.get("content", "")
        else:
            return f"Error: {result.get('content', 'Unknown error')}"
    except requests.exceptions.RequestException as e:
        return f"Error: Server request failed: {str(e)}"

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
        print(f"[ImageTool] Screen capture error: {e}")
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

