import os
import json
import httpx


GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/gemini-2.5-flash:generateContent"
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
    Ask Gemini to generate a standalone Python script that can:
    - Use httpx, pandas, numpy, matplotlib, pypdf, networkx, and stdlib
    - Download / parse data (CSV, JSON, PDF, API, HTML)
    - Compute the quiz answer
    - POST JSON {email, secret, url, answer} to submit_url
    - Print ONLY the JSON submission response to stdout
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
        "Task requirements for the script you output:\n"
        f"1. Treat the quiz URL as: {quiz_url}\n"
        f"2. Treat the submit URL as: {submit_url}\n"
        f"3. Use the email: {email}\n"
        f"4. Use the secret: {secret}\n"
        "5. You may use ONLY the libraries listed above plus the Python standard library.\n"
        "6. Download or access any data required by the quiz (PDF/CSV/JSON/API/HTML):\n"
        "   - Use httpx for HTTP requests (always set timeout=30.0).\n"
        "   - For CSV/Excel-like tables, use pandas.\n"
        "   - For JSON, use json.loads.\n"
        "   - For PDF files, use pypdf to extract text.\n"
        "   - For network analysis (graphs), use networkx.\n"
        "   - For statistical or numeric work, use numpy or the statistics module.\n"
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
        "Submission:\n"
        "10. Always send a POST request to the submit URL with JSON body EXACTLY:\n"
        "    {\n"
        "      \"email\": email,\n"
        "      \"secret\": secret,\n"
        "      \"url\": quiz_url,\n"
        "      \"answer\": ANSWER\n"
        "    }\n"
        "    The key MUST be named \"answer\" (singular). Do not use any other key name.\n"
        "11. After receiving the submission response, do:\n"
        "      resp_json = response.json()\n"
        "      print(json.dumps(resp_json))\n"
        "    i.e., print ONLY a single line of JSON to stdout. No extra logs or text.\n\n"
        "Code structure:\n"
        "12. Put all logic in functions and a main() function.\n"
        "13. Protect execution with:\n"
        "    if __name__ == '__main__':\n"
        "        main()\n"
        "14. Do NOT include triple backticks or markdown. Output pure Python code only.\n"
        "15. Even if an error occurs (e.g., network timeout, bad data), catch the exception and\n"
        "    still print a JSON object with keys: \"correct\" (false), \"url\" (null or next URL\n"
        "    if the API provides one), and \"reason\" (the error message).\n"
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

    # Shorter timeout to keep things snappy
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(GEMINI_ENDPOINT, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

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
