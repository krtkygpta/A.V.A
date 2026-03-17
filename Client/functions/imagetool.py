import pyautogui
import datetime
import os
import cv2
import requests
import json


def capture_and_send_image(folder="captured_images"):
    """Capture an image from the camera and return the file path."""
    if not os.path.exists(folder):
        os.makedirs(folder)
    
    camera = cv2.VideoCapture(1)
    
    if not camera.isOpened():
        # Try default camera (index 0) as fallback
        camera = cv2.VideoCapture(0)
        if not camera.isOpened():
            print("Error: Could not open camera")
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
        print("Error: Could not capture frame from camera")
        return None


def sendtoserver(image_path, data):
    """Send an image to the server for processing."""
    try:
        with open(image_path, 'rb') as image_file:
            response = requests.post(
                'http://127.0.0.1:5000/edith/imagetool',
                files={'image': image_file},  # Fixed: use the already opened file handle
                data=data
            )
            return response.text
    except Exception as e:
        return json.dumps({'status': 'error', 'content': f"Error sending image: {str(e)}"})


def capture_screen():
    """Capture a screenshot and return the file path."""
    # Create directory if it doesn't exist
    screen_folder = "captured_screens"
    if not os.path.exists(screen_folder):
        os.makedirs(screen_folder)
    
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    screen_path = os.path.join(screen_folder, f"{timestamp}.jpg")

    try:
        screenshot = pyautogui.screenshot()
        screenshot.save(screen_path)
        return screen_path
    except Exception as e:
        print(f"Error capturing screen: {e}")
        return None


def image_tool(tool, query):
    """Tool to analyze images from camera or screen."""
    data = {
        'tool': 'image_tool',
        'query': query,
    }
    
    if tool == 'camera':
        path = capture_and_send_image()
        if path is None:
            return json.dumps({'status': 'error', 'content': 'Failed to capture camera image'})
        response = sendtoserver(image_path=path, data=data)
        return response
    elif tool == 'screen':
        path = capture_screen()
        if path is None:
            return json.dumps({'status': 'error', 'content': 'Failed to capture screenshot'})
        response = sendtoserver(image_path=path, data=data)
        return response
    else:
        return json.dumps({'status': 'error', 'content': f'Unknown tool: {tool}. Use "camera" or "screen"'})


# image_tool('camera')