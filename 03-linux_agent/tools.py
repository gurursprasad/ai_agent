import os
import datetime
import subprocess
from urllib import response


BLACKLISTED_KEYWORDS = ["rm", "shutdown", "reboot", "mkfs", ":(){", "dd", "chmod 777", "curl http", "wget http", "scp", "mv /", "kill -9 1"]


def get_current_directory():
    """
    Get the current working directory.
    """
    return os.getcwd()
   

def run_command(command: str) -> str:
    try:
        result = subprocess.run(
            command,
            shell="/bin/bash",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        return result.stdout + result.stderr
    except Exception as e:
        return f"Error: {str(e)}"


def log_command_history(prompt: str, command: str, output: str, log_file="command_history.log"):
    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_file, "a") as f:
            f.write(f"[{timestamp}]\n")
            f.write(f"Prompt   : {prompt}\n")
            f.write(f"Command  : {command}\n")
            f.write(f"Output   :\n{output}\n")
            f.write("-" * 60 + "\n")
    except Exception as e:
        print(f"Failed to log command history: {str(e)}")


def is_command_safe(command):
    for keyword in BLACKLISTED_KEYWORDS:
        if keyword in command:
            return False
    return True
