import os
import time
import json
import httpx

# Primary + fallback models
GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/gemini-2.5-flash:generateContent"
)
FALLBACK_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/gemini-1.5-pro:generateContent"
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
        raise GeminiConfigError("Missing GEMINI_API_KEY or GOOGLE_API_KEY in environment.")
    return api_key


def _call_gemini(endpoint: str, body: dict, api_key: str) -> dict:
    """
    Low-level wrapper with small retry logic.
    """
    headers = {
        "Content-Type": "application/json",
    }
    url_with_key = f"{endpoint}?key={api_key}"

    last_error = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(url_with_key, headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json()
                return data
        except httpx.HTTPStatusError as e:
            last_error = e
            status = e.response.status_code
            # Retry only on 5xx
            if 500 <= status < 600 and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
        except httpx.RequestError as e:
            last_error = e
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise

    # Should not reach here normally
    raise RuntimeError(f"Failed to call Gemini after retries: {last_error}")


def _extract_code_from_response(data: dict) -> str:
    """
    Extract the code text from the Gemini response.

    Handles the standard generateContent shape:
    {
      "candidates": [
        {
          "content": {
            "parts": [
              {"text": "...python code..."}
            ]
          },
          "finishReason": "...",
          ...
        }
      ],
      "usageMetadata": {...}
    }
    """
    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError(f"Gemini returned no candidates: raw={data!r}")

    first = candidates[0]
    finish_reason = first.get("finishReason")
    content = first.get("content", {}) or {}
    parts = content.get("parts", []) or []

    # Collect all text parts
    text_chunks = []
    for part in parts:
        if isinstance(part, dict) and "text" in part:
            text_chunks.append(part["text"])

    if not text_chunks:
        # If we hit MAX_TOKENS with zero visible text, surface a clear error
        raise RuntimeError(
            f"Gemini returned no text (finishReason={finish_reason}); raw={data!r}"
        )

    code = "\n".join(text_chunks).strip()

    # Strip ``` fences if present
    if code.startswith("```"):
        # Remove leading ```
        code = code.lstrip("`").lstrip()
        if code.lower().startswith("python"):
            code = code[6:].lstrip()
        # Remove trailing ```
        if code.endswith("```"):
            code = code[:-3].rstrip()

    return code.strip()


def generate_solver_script(
    quiz_context: str,
    quiz_url: str,
    submit_url: str,
    email: str,
    secret: str,
) -> str:
    """
    Ask Gemini to generate a standalone Python script that:

    - Uses httpx/pandas/numpy/etc. as needed to solve the quiz.
    - Computes the quiz ANSWER.
    - Prints ONLY a single line of JSON like: {"answer": ...}
      via: print(json.dumps({"answer": ANSWER}))
    - ANSWER must be a JSON-serialisable scalar (number/string/bool) or a
      base64 string if a file/image is required.
    - MUST NOT send the final POST to submit_url; that is done by the caller.
    """

    api_key = _get_gemini_api_key()

    # Keep the instruction short to reduce prompt tokens and avoid blowing
    # the hidden "thoughts" budget.
    # quiz_context is already truncated in solver; keep it short here too.
    trimmed_context = quiz_context[:4000]

    prompt = (
        "You are a Python code generator.\n"
        "Write a COMPLETE Python 3 script (no comments outside code, no markdown) "
        "that solves the data quiz described in the text below.\n\n"
        "REQUIREMENTS:\n"
        "1. The script may use these libraries if needed: httpx, json, csv, re, "
        "   pandas, numpy, matplotlib, pypdf, networkx, base64.\n"
        "2. Use httpx (timeout=30.0) for HTTP downloads. For CSV files, prefer "
        "   pandas.read_csv with safe options (on_bad_lines='skip', engine='python').\n"
        "3. Carefully read the quiz text to identify:\n"
        "   - What needs to be computed (e.g., sum/mean, filtering, etc.).\n"
        "   - Any file/URL you must download (CSV, JSON, PDF, etc.).\n"
        "4. Compute the correct answer programmatically.\n"
        "5. At the end of main(), build a dict:\n"
        "       result = {\"answer\": ANSWER}\n"
        "   where ANSWER is a scalar (number/string/bool), or a base64-encoded\n"
        "   string if the answer is an image/file.\n"
        "6. Print EXACTLY ONE line to stdout:\n"
        "       import json\n"
        "       print(json.dumps(result))\n"
        "   No other prints or logging.\n"
        "7. Do NOT send any HTTP POST to the quiz submit URL. Only compute locally.\n"
        "8. Wrap everything in a main() function and guard with:\n"
        "       if __name__ == '__main__':\n"
        "           main()\n"
        "9. On any exception, catch it and still print a JSON object with\n"
        "   at least: {\"answer\": null, \"error\": \"...\"}.\n\n"
        f"QUIZ PAGE URL: {quiz_url}\n"
        f"(Submit URL is provided for reference only; do NOT POST to it: {submit_url})\n\n"
        "QUIZ PAGE TEXT:\n"
        f"{trimmed_context}\n"
    )

    body = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ],
        "generationConfig": {
            # Low randomness, small output, no extra 'thinking' budget
            "temperature": 0.0,
            "topP": 0.9,
            "topK": 40,
            "maxOutputTokens": 1024,
            "responseMimeType": "text/plain",
            # Try to minimise hidden reasoning tokens so we actually get code
            "thinkingConfig": {
                "thinkingBudget": 0
            },
        },
    }

    # 1) Try 2.5-flash
    try:
        data = _call_gemini(GEMINI_ENDPOINT, body, api_key)
        code = _extract_code_from_response(data)
        if code:
            return code
    except Exception as e:
        # Log to stderr but fall through to fallback model
        print(f"[generate_solver_script] 2.5-flash failed: {type(e).__name__}: {e}", flush=True)

    # 2) Fallback to 1.5-pro
    try:
        data = _call_gemini(FALLBACK_ENDPOINT, body, api_key)
        code = _extract_code_from_response(data)
        if code:
            return code
    except Exception as e:
        print(f"[generate_solver_script] 1.5-pro fallback failed: {type(e).__name__}: {e}", flush=True)

    # 3) Ultimate fallback: trivial script so the pipeline doesn't crash
    fallback_code = '''\
import json

def main():
    # Fallback: no answer computed
    result = {"answer": None, "error": "LLM generation failed"}
    print(json.dumps(result))

if __name__ == "__main__":
    main()
'''
    return fallback_code
