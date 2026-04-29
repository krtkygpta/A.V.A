import os
import json
import subprocess
import threading
import queue

PERMISSIONS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'bash_permissions.json')
SESSION_COMMANDS = set()

def _load_permissions():
    if os.path.exists(PERMISSIONS_FILE):
        with open(PERMISSIONS_FILE, 'r') as f:
            return json.load(f)
    return {'always_allowed': [], 'denied': []}

def _save_permissions(perms):
    with open(PERMISSIONS_FILE, 'w') as f:
        json.dump(perms, f, indent=2)

def _get_command_type(command_str):
    parts = command_str.strip().lower().split()
    return parts[0] if parts else ""

def _check_permission(command_str):
    perms = _load_permissions()
    cmd_normalized = command_str.strip().lower()
    cmd_type = _get_command_type(command_str)
    
    for denied in perms.get('denied', []):
        denied_type = _get_command_type(denied)
        if denied_type == cmd_type:
            return False
    
    for allowed in perms.get('always_allowed', []):
        allowed_type = _get_command_type(allowed)
        if allowed_type == cmd_type:
            return True
    
    if cmd_type in SESSION_COMMANDS:
        return True
    
    return None

def _prompt_and_get_result(command_str):
    result_queue = queue.Queue()
    cmd_type = _get_command_type(command_str)
    
    def prompt():
        import tkinter as tk
        from tkinter import messagebox
        
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        
        response = messagebox.askyesnocancel(
            "AVA Bash Permission",
            f"Allow command type: {cmd_type}?\n\nExample: {command_str}\n\nYES = Always allow this type\nNO = Allow this session only\nCANCEL = Deny this type",
            icon="warning"
        )
        root.destroy()
        
        if response is True:
            perms = _load_permissions()
            cmd = command_str.strip()
            perms.setdefault('always_allowed', []).append(cmd)
            perms['denied'] = [d for d in perms.get('denied', []) if _get_command_type(d) != cmd_type]
            _save_permissions(perms)
            result_queue.put("allowed")
        elif response is False:
            SESSION_COMMANDS.add(cmd_type)
            result_queue.put("session")
        else:
            perms = _load_permissions()
            perms.setdefault('denied', []).append(command_str.strip())
            perms['always_allowed'] = [a for a in perms.get('always_allowed', []) if _get_command_type(a) != cmd_type]
            _save_permissions(perms)
            result_queue.put("denied")
    
    thread = threading.Thread(target=prompt, daemon=True)
    thread.start()
    thread.join()
    
    try:
        return result_queue.get_nowait()
    except queue.Empty:
        return "denied"

def run_bash(command: str, timeout: int = 30) -> str:
    status = _check_permission(command)
    
    if status is False:
        return f"[error] Permission denied: {command}"
    
    if status is None:
        perm_result = _prompt_and_get_result(command)
        if perm_result == "denied":
            return f"[error] Permission denied: {command}"
        status = True
    
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        output = result.stdout + result.stderr
        return output.strip() if output.strip() else "[no output]"
    except subprocess.TimeoutExpired:
        return f"[error] Command timed out after {timeout} seconds"
    except FileNotFoundError:
        return f"[error] Command not found: {command.split()[0]}"
    except Exception as e:
        return f"[error] {str(e)}"

def list_permissions():
    perms = _load_permissions()
    perms['session_allowed'] = list(SESSION_COMMANDS)
    return json.dumps(perms, indent=2)

def clear_session():
    SESSION_COMMANDS.clear()
    return "Session cleared"