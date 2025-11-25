import os
import sys
import json
import tempfile
import subprocess
from typing import Any, Dict, Optional


def run_script(code: str) -> Dict[str, Any]:
    """
    Save `code` to a temp Python file, execute it in a subprocess,
    and parse stdout as JSON if possible. If stdout is not JSON,
    wrap it into a default error response.
    """
    with tempfile.NamedTemporaryFile(
        suffix=".py", delete=False, mode="w", encoding="utf-8"
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
            timeout=90,
        )
    except subprocess.TimeoutExpired as e:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"TimeoutExpired: {e}",
            "response": {
                "correct": False,
                "url": None,
                "reason": f"Script timeout: {e}",
            },
        }

    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()

    parsed: Optional[Dict[str, Any]] = None
    if stdout:
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            # Wrap raw stdout into a structured error response
            parsed = {
                "correct": False,
                "url": None,
                "reason": stdout,
            }
    else:
        # No stdout at all
        parsed = {
            "correct": False,
            "url": None,
            "reason": stderr or "Script produced no output",
        }

    return {
        "returncode": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "response": parsed,
    }
