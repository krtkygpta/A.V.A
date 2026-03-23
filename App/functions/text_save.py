import os
import subprocess
import json


def create_file(data, filename, location, extension):
    """Create a file with the given data, filename, location, and extension."""
    if not filename.endswith(extension):
        filename += "." + extension
    full_path = os.path.join(location, filename)
    try:
        # Create directory if it doesn't exist
        os.makedirs(location, exist_ok=True)
        with open(full_path, 'w', encoding='utf-8') as file:
            file.write(data)
        return json.dumps({'status': 'success', 'content': f"File saved to {full_path}"})
    except Exception as e:
        return json.dumps({'status': 'error', 'content': f"Cannot save file: {str(e)}"})


def save_text(text, filename, location):
    """Save text content to a file with a custom name (appends .txt if needed)."""
    if not filename.endswith('.txt'):
        filename += '.txt'
    full_path = os.path.join(location, filename)
    try:
        # Create directory if it doesn't exist
        os.makedirs(location, exist_ok=True)
        with open(full_path, 'w', encoding='utf-8') as file:
            file.write(text)
        return json.dumps({'status': 'success', 'content': f"Text saved to {full_path}"})
    except Exception as e:
        return json.dumps({'status': 'error', 'content': f"Cannot save text: {str(e)}"})


def open_file(filename, location):
    """Open a file using the default system application."""
    full_path = os.path.join(location, filename)
    try:
        subprocess.Popen(['start', '', full_path], shell=True)
        return json.dumps({'status': 'success', 'content': f"Opened {full_path}"})
    except Exception as e:
        return json.dumps({'status': 'error', 'content': f"Cannot open file: {str(e)}"})


def delete_file(filename, location):
    """Delete a file at the given location."""
    full_path = os.path.join(location, filename)
    try:
        os.remove(full_path)
        return json.dumps({'status': 'success', 'content': f"File {full_path} deleted successfully"})
    except Exception as e:
        return json.dumps({'status': 'error', 'content': f"Cannot delete file: {str(e)}"})


def list_files(location):
    """List all files in a directory."""
    try:
        files = os.listdir(location)
        return json.dumps({'status': 'success', 'content': files})
    except Exception as e:
        return json.dumps({'status': 'error', 'content': f"Cannot list files: {str(e)}"})