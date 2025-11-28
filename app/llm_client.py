import os
import json
import httpx


# Allow model override via env if you want to try a stronger one (e.g. gemini-2.5-pro)
GEMINI_MODEL_NAME = (
    os.environ.get("GEMINI_MODEL_NAME") or "gemini-2.5-flash"
)

GEMINI_ENDPOINT = (
    f"https://generativelanguage.googleapis.com/v1beta/"
    f"models/{GEMINI_MODEL_NAME}:generateContent"
)


class GeminiConfigError(RuntimeError):
    """Raised when Gemini API configuration is missing or invalid."""
    pass


def _get_gemini_api_key() -> str:
    """
    Return the Gemini / Google API key from environment.

    Uses GEMINI_API_KEY or GOOGLE_API_KEY.
    """
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise GeminiConfigError(
            "Missing GEMINI_API_KEY or GOOGLE_API_KEY in environment."
        )
    return api_key


def generate_solver_script(
    quiz_context: str,
    quiz_url: str,
    submit_url: str,
    email: str,
    secret: str,
) -> str:
    """
    Ask Gemini to generate a standalone Python script that:

    - Uses httpx, pandas, numpy, matplotlib, pypdf, networkx, and stdlib.
    - Downloads / parses any referenced data (CSV, JSON, PDF, API, HTML, audio, etc.).
    - Computes the quiz ANSWER.
    - Prints ONLY a single line of JSON like: {"answer": ...}
      (optionally plus other keys, but "answer" MUST always be present).
    - DOES NOT send the final POST to submit_url; that is handled by the orchestrator.
    """

    api_key = _get_gemini_api_key()

    # Instruction block for Gemini
    system_instruction = (
        "You are an expert Python programmer. "
        "You will be given a data-related quiz description and must output ONLY "
        "a complete Python script (no explanation, no markdown).\n\n"
        "Environment:\n"
        "- Python 3\n"
        "- The following libraries are available and already installed:\n"
        "  httpx, pandas, numpy, matplotlib, pypdf, networkx, json, csv, base64, re.\n\n"
        "Context values passed from the orchestrator (for reference only):\n"
        f"- quiz_url: {quiz_url}\n"
        f"- submit_url (fallback, DO NOT POST TO IT): {submit_url}\n"
        f"- email: {email}\n"
        f"- secret: {secret}\n\n"
        "You MAY use quiz_url or other URLs mentioned in the quiz text to download data\n"
        "if needed, but you MUST NOT send the final answer to ANY submit endpoint.\n"
        "Your only job is to compute the answer.\n\n"
        "Task requirements for the script you output:\n"
        "1. Use httpx for HTTP requests (always set timeout=30.0) when downloading data\n"
        "   such as CSV/JSON/PDF/HTML/audio links mentioned in the quiz.\n"
        "2. For CSV/Excel-like tables, use pandas.read_csv with robust options,\n"
        "   e.g. on_bad_lines='skip' and engine='python', so the script does not\n"
        "   crash on malformed rows. If parsing fails, catch the exception.\n"
        "3. For JSON, use json.loads.\n"
        "4. For PDF files, use pypdf to extract text.\n"
        "5. For network analysis (graphs), use networkx.\n"
        "6. For statistical or numeric work, use numpy or the statistics module.\n"
        "7. If the quiz asks for a plot, chart, or visualization:\n"
        "   - Use matplotlib to generate the figure.\n"
        "   - Save it to a PNG file.\n"
        "   - Open the PNG file in binary mode, base64-encode its contents, and use that\n"
        "     base64 string as the value of 'answer'.\n"
        "8. If the quiz asks for a file attachment (CSV, PPTX, image, etc.):\n"
        "   - Generate the file programmatically if feasible.\n"
        "   - Read the file as bytes.\n"
        "   - Base64-encode the bytes and use the resulting string as 'answer'.\n"
        "9. For simpler answers (number, boolean, or text string):\n"
        "   - Set 'answer' directly to that value.\n\n"
        "EXTREMELY IMPORTANT – ANSWER KEY REQUIREMENT:\n"
        "10. The final JSON you print MUST ALWAYS contain a top-level key named \"answer\".\n"
        "    This is non-negotiable:\n"
        "    - Never rename it.\n"
        "    - Never omit it.\n"
        "    - Even on errors or partial progress, you MUST still include \"answer\".\n"
        "11. If you cannot confidently compute the true answer, set:\n"
        "      answer = None\n"
        "    but still print JSON with an \"answer\" key and include an \"error\" key\n"
        "    explaining what went wrong.\n\n"
        "Optional extra fields (for debugging):\n"
        "12. You MAY add extra keys to the result dict, such as:\n"
        "      result[\"debug\"] = \"some debug info\"\n"
        "      result[\"submit_url\"] = inferred_submit_url_or_None  # if you parse it\n"
        "    but the \"answer\" key MUST always exist.\n\n"
        "Output format (MANDATORY):\n"
        "13. At the end of main(), compute the final ANSWER and build a dict like:\n"
        "      result = {\"answer\": ANSWER}\n"
        "      # Optionally:\n"
        "      # result[\"debug\"] = \"...\"\n"
        "      # result[\"submit_url\"] = inferred_submit_url_or_None\n"
        "14. Then print EXACTLY one line to stdout:\n"
        "      print(json.dumps(result))\n"
        "    No other prints, logs, or text. No pretty-printing. No extra lines.\n\n"
        "Code structure:\n"
        "15. Put all logic in functions and a main() function.\n"
        "16. Protect execution with:\n"
        "    if __name__ == '__main__':\n"
        "        main()\n"
        "17. Do NOT include triple backticks or markdown. Output pure Python code only.\n"
        "18. Wrap the main logic in try/except so that on ANY exception you still do:\n"
        "      result = {\"answer\": None, \"error\": str(e)}\n"
        "      print(json.dumps(result))\n"
        "    i.e., even on failure, you MUST print a JSON object with an \"answer\" key.\n"
    )

    full_prompt = (
        system_instruction
        + "\n\nHere is the quiz page text/context:\n\n"
        + quiz_context
    )

    body = {
        "contents": [
            {
                "parts": [
                    {
                        "text": full_prompt
                    }
                ]
            }
        ]
    }

    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
    }

    # --- Gemini call with simple retry on transient errors ---
    last_error = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(GEMINI_ENDPOINT, headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json()
            break  # success
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            last_error = e
            # Retry only on 5xx errors
            if 500 <= status < 600 and attempt < 2:
                import time as _time
                _time.sleep(1.5 * (attempt + 1))
                continue
            raise
        except httpx.RequestError as e:
            last_error = e
            if attempt < 2:
                import time as _time
                _time.sleep(1.5 * (attempt + 1))
                continue
            raise

    if last_error is not None and "data" not in locals():
        raise RuntimeError(f"Failed to call Gemini after retries: {last_error}")

    # Extract text from Gemini response
    try:
        candidates = data["candidates"]
        first = candidates[0]
        parts = first["content"]["parts"]
        code = parts[0]["text"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"Unexpected Gemini response format: {e}, raw={data!r}")

    # In case Gemini wraps code in ```python fences, strip them
    stripped = code.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        stripped = stripped.lstrip()
        if stripped.lower().startswith("python"):
            stripped = stripped[6:].lstrip()

    return stripped
