import os
import sys
import json
import tempfile
import subprocess
from typing import Any, Dict, Optional


def run_script(code: str) -> Dict[str, Any]:
    """
    Save `code` to a temp Python file, execute it in a subprocess,
    and parse stdout as JSON if possible.

    Guarantees:
    - The returned dict has keys:
        - returncode: int
        - stdout: str
        - stderr: str
        - response: dict
    - `response` will ALWAYS be a dict.
    - If `response` has no 'answer' and no 'correct', we wrap it as:
        {'answer': <original_response_dict>}
      so that the caller can always find an 'answer' key.
    """
    # Write the generated code to a temporary file
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
        # On timeout, return a structured error with answer=None
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"TimeoutExpired: {e}",
            "response": {
                "answer": None,
                "error": f"Script timeout: {e}",
            },
        }

    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()

    parsed: Optional[Any] = None

    if stdout:
        # Try to interpret stdout as JSON
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            # stdout wasn't valid JSON; treat it as an error message
            parsed = {
                "answer": None,
                "error": stdout,
            }
    else:
        # No stdout at all; treat stderr or a default message as error
        parsed = {
            "answer": None,
            "error": stderr or "Script produced no output",
        }

    # At this point, `parsed` might not be a dict (e.g. a list or a raw value)
    if not isinstance(parsed, dict):
        parsed = {"answer": parsed}

    # Key part:
    # If the script's JSON has neither 'answer' nor 'correct',
    # treat the entire object as the answer payload.
    # This keeps 'correct' free for the "script already submitted" case.
    if "answer" not in parsed and "correct" not in parsed:
        parsed = {"answer": parsed}

    return {
        "returncode": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "response": parsed,
    }
