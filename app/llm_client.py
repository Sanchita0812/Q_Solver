import os
import json
import httpx

GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/gemini-2.5-flash:generateContent"
)
#Endpoint + header pattern from official docs :contentReference[oaicite:0]{index=0}


class GeminiConfigError(RuntimeError):
    pass


def _get_gemini_api_key() -> str:
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise GeminiConfigError(
            "Missing GEMINI_API_KEY / GOOGLE_API_KEY in environment."
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
    - Uses httpx
    - Computes the answer to the quiz
    - POSTs JSON to submit_url with: email, secret, url, answer
    - Prints the submission response JSON to stdout (and nothing else)
    """
    api_key = _get_gemini_api_key()

    system_instruction = (
        "You are an expert Python programmer. "
        "You will be given a data-related quiz description and must output ONLY "
        "a complete Python script (no explanation, no markdown).\n"
        "When run, this script must:\n"
        f"1. Treat the quiz URL as: {quiz_url}\n"
        f"2. Treat the submit URL as: {submit_url}\n"
        f"3. Use the email: {email}\n"
        f"4. Use the secret: {secret}\n"
        "5. Use ONLY the 'httpx' library for HTTP requests (assume it is installed).\n"
        "6. Download or access any data required (PDF/CSV/API/HTML) as described.\n"
        "7. Compute the correct 'answer' for the quiz.\n"
        "8. Send a POST request to the submit URL with JSON body:\n"
        "   {\"email\": email, \"secret\": secret, \"url\": quiz_url, \"answer\": ANSWER}\n"
        "9. Print ONLY the JSON response from the submit request to stdout using print().\n"
        "   No extra text, logs, or explanation.\n"
        "Use a main() function and protect execution with:\n"
        "   if __name__ == '__main__':\n"
        "       main()\n"
        "Do not include triple backticks or markdown. Output pure Python.\n"
    )

    #Gemini REST Request body as per the official docs
    body = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": (
                            system_instruction
                            + "\n\nHere is the quiz page text/context:\n\n"
                            + quiz_context
                        )
                    }
                ],
            }
        ]
    }

    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=60.0) as client:
        resp = client.post(GEMINI_ENDPOINT, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

    try:
        candidates = data["candidates"]
        first = candidates[0]
        parts = first["content"]["parts"]
        code = parts[0]["text"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"Unexpected Gemini response format: {e}, raw={data!r}")

    #in case Gemini wraps code in ```python fences, stripping them
    if code.strip().startswith("```"):
        code = code.strip().strip("`")
        #crude fence stripping: removing leading 'python' if present
        if code.lstrip().lower().startswith("python"):
            code = code.lstrip()[6:]

    return code.strip()
