import sys
import json
import tempfile
import subprocess
import os
from typing import Any, Dict


def run_script(code: str) -> Dict[str, Any]:
    """
    Save `code` to a temp Python file, execute it in a subprocess,
    and parse stdout as JSON if possible.

    Always returns a dict with keys:
    - returncode: int
    - stdout: str
    - stderr: str
    - response: dict with at least an 'answer' key (may be None)
    """
    with tempfile.NamedTemporaryFile(
        suffix=".py",
        delete=False,
        mode="w",
        encoding="utf-8",
    ) as f:
        script_path = f.name
        f.write(code)

    env = os.environ.copy()

    try:
        proc = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            env=env,
            timeout=20,  # strict timeout for the generated script
        )
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        returncode = proc.returncode
    except subprocess.TimeoutExpired as e:
        stdout = ""
        stderr = f"TimeoutExpired: {e}"
        returncode = -1
        response = {
            "answer": None,
            "error": "Timeout",
        }
        # Clean up file before returning
        if os.path.exists(script_path):
            os.remove(script_path)
        return {
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
            "response": response,
        }
    except Exception as e:
        stdout = ""
        stderr = f"Script execution error: {e}"
        returncode = -1
        response = {
            "answer": None,
            "error": str(e),
        }
        if os.path.exists(script_path):
            os.remove(script_path)
        return {
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
            "response": response,
        }
    finally:
        if os.path.exists(script_path):
            try:
                os.remove(script_path)
            except OSError:
                pass

    # Try to parse stdout as JSON
    try:
        parsed = json.loads(stdout) if stdout else {}
        # If script printed a bare scalar (number/string), wrap as answer
        if not isinstance(parsed, dict):
            parsed = {"answer": parsed}
    except json.JSONDecodeError:
        # If script printed plain text and exited cleanly, treat it as the answer
        if returncode == 0 and stdout:
            parsed = {"answer": stdout}
        else:
            parsed = {
                "answer": None,
                "error": stderr or stdout or "Script produced no usable output",
            }

    # Ensure there is at least an 'answer' key in response
    if "answer" not in parsed:
        parsed["answer"] = None

    return {
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "response": parsed,
    }
