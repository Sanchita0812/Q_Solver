import os
import json
import time
import httpx

# Primary + fallback Gemini endpoints
PRIMARY_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/gemini-1.5-pro:generateContent"
)
FALLBACK_ENDPOINT = (
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
    Ask Gemini to generate a standalone Python script that:

    - Uses only: httpx, pandas, numpy, matplotlib, pypdf, networkx, json, csv, base64, re,
      plus the Python stdlib.
    - Downloads / parses any referenced data (CSV, JSON, PDF, API, HTML) as required
      by the quiz description.
    - Computes the quiz ANSWER *programmatically* from the downloaded / parsed data.
    - Prints ONLY a single line of JSON to stdout, e.g.:
        {"answer": 12345, "trace": {...}}
      where:
        - "answer" is a scalar (number, string, or boolean), NOT a dict or list.
        - "trace" is optional and may contain debug info for our logs.
    - DOES NOT send the final POST to submit_url; the orchestrator handles submission.

    The secret value is never to be printed, logged, or sent anywhere by the script.
    """

    api_key = _get_gemini_api_key()

    # Strong instruction block for Gemini
    system_instruction = f"""
You are an expert Python programmer. Your job is to write a Python 3 script
that solves a data quiz. You MUST output ONLY valid Python code (no markdown,
no explanations, no surrounding text).

Context:
- quiz_url: {quiz_url}
- submit_url (handled by caller, DO NOT POST HERE): {submit_url}
- email: {email}
- secret: (provided to the caller only; you MUST NOT print, log, or send it)

The quiz description and any relevant text/HTML is:

{quiz_context}

---------------------- REQUIREMENTS FOR THE GENERATED SCRIPT ----------------------

1. Imports:
   - You may use ONLY these third-party libraries:
       httpx, pandas, numpy, matplotlib, pypdf, networkx, json, csv, base64, re
     plus the Python standard library.
   - Always import json, since you must print JSON at the end.

2. Data access:
   - To download any files or HTML mentioned in the quiz, use httpx with timeout=30.0.
   - For CSV/TSV/etc:
       - Use pandas.read_csv with robust options:
         on_bad_lines='skip', engine='python' where appropriate.
       - Convert columns to numeric with pandas.to_numeric(..., errors='coerce') as needed.
   - For JSON:
       - Use response.json() or json.loads(response.text).
   - For PDF files:
       - Use pypdf to read pages and extract text.
   - For graph/network questions:
       - Use networkx.
   - For numeric/statistical work:
       - Use numpy or the statistics module from stdlib.

3. NO GUESSING:
   - You MUST NOT guess or hard-code final numeric/boolean answers.
   - The script MUST:
       (a) Download and parse the actual data described in the quiz_context.
       (b) Compute the answer from that data.
   - If the quiz asks for "sum of a column":
       - Load the data into pandas.
       - Ensure the target column is numeric via to_numeric(..., errors='coerce').
       - Drop NaNs and compute sum() on that column.
   - If you cannot download or parse the data after reasonable attempts,
     catch the exception and set answer = None and include an "error" string
     in the JSON you print.

4. Sanity checks:
   - Before deciding on the final answer, perform simple checks:
       - For sums, verify you aggregated at least 1 numeric value.
       - For counts, verify that the filtered DataFrame is not empty.
   - If a sanity check fails, set answer = None and include a brief error message.

5. Output format (VERY IMPORTANT):
   - At the end of the script, you MUST:
       - Build a Python dict named result, for example:
           result = {{
               "answer": ANSWER,
               "trace": {{
                   "step": "description",
                   "rows_processed": N,
                   "file_url": "https://..."
               }}
           }}
         where:
           - "answer" is REQUIRED.
           - "trace" is OPTIONAL and may contain any useful debug info.
   - The value of result["answer"] MUST be one of:
       - a number (int/float),
       - a string,
       - or a boolean.
     It MUST NOT be a dict or list.
   - If the quiz requires a file or image as the answer:
       - Generate the file.
       - Read it as bytes.
       - Base64-encode it into a string.
       - Use that base64 string as result["answer"].
   - If an error occurs that prevents computing the answer, set:
       - result["answer"] = None
       - result["error"] = "<short explanation>"

   - Finally, print EXACTLY one line to stdout:
       import json
       print(json.dumps(result))
     No other print statements are allowed.

6. Code structure:
   - Put most logic into functions plus a main() function.
   - Protect execution with:
       if __name__ == "__main__":
           main()

7. Secrets and submission:
   - You MUST NOT:
       - POST to submit_url or any /submit endpoint.
       - Include the secret value in any variable, string, or output.
   - Your only responsibility is to compute and print the JSON described above.

Remember:
- Output MUST be plain Python code only (no ``` fences).
- The script must be directly executable under Python 3.
"""

    body = {
        "contents": [
            {
                "parts": [
                    {
                        "text": system_instruction
                    }
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "topP": 0.9,
            "topK": 40,
            "maxOutputTokens": 8192,
        },
    }

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }

    # Simple retry with exponential backoff, switching endpoint if needed
    last_error = None
    for attempt in range(3):
        try:
            if attempt == 0:
                endpoint = PRIMARY_ENDPOINT
            elif attempt == 1:
                endpoint = PRIMARY_ENDPOINT
            else:
                endpoint = FALLBACK_ENDPOINT

            url_with_key = f"{endpoint}?key={api_key}"

            with httpx.Client(timeout=60.0) as client:
                resp = client.post(url_with_key, headers=headers, json=body)
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

            # Strip possible ```python fences
            stripped = code.strip()
            if stripped.startswith("```"):
                # Remove starting fence
                if stripped.lower().startswith("```python"):
                    stripped = stripped[9:]
                else:
                    stripped = stripped[3:]
                # Remove ending fence if present
                if stripped.endswith("```"):
                    stripped = stripped[:-3]
                stripped = stripped.strip()

            if not stripped:
                raise RuntimeError("Empty code returned from Gemini")

            return stripped

        except (httpx.HTTPStatusError, httpx.RequestError, RuntimeError) as e:
            last_error = e
            # Retry on 5xx or network-ish issues; otherwise break
            if isinstance(e, httpx.HTTPStatusError):
                status = e.response.status_code
                if 500 <= status < 600 and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                else:
                    break
            else:
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                else:
                    break

    # Fallback script if Gemini fails completely
    # This keeps the pipeline alive but returns a dummy answer.
    fallback_code = '''import json

def main():
    result = {
        "answer": None,
        "error": "Failed to generate solver script from LLM."
    }
    print(json.dumps(result))

if __name__ == "__main__":
    main()
'''
    return fallback_code
