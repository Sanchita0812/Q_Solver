import time
import logging
import re
import json
import httpx
from urllib.parse import urlparse
from typing import Optional, Tuple
from bs4 import BeautifulSoup

from .browser import fetch_rendered_html
from .llm_client import generate_solver_script
from .script_runner import run_script

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _extract_question_and_submit_url(html: str, quiz_url: str) -> Tuple[str, str]:
    """
    Parse the rendered HTML to get:
    - quiz_context: trimmed page text for the LLM
    - submit_url: endpoint to POST the answer to

    Strategy for submit_url:
    - Extract ALL http(s) URLs from the raw HTML (href/src/action/etc).
    - Drop the quiz_url itself.
    - Drop obvious static assets (.js, .css, images, etc.).
    - Prefer URLs containing 'submit', 'answer', or 'quiz'.
    - Avoid the bare domain (no path or just '/'); if we only see that,
      convert it to https://host/submit.
    - Fallback: derive https://<host>/submit from quiz_url.
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n")

    lines = [line.strip() for line in text.splitlines() if line.strip()]

    # Optional question line detection (kept for context, but not strictly used)
    question_line = ""
    for line in lines:
        if re.match(r"^Q\d+[\.:]", line):
            question_line = line
            break
    if not question_line and lines:
        question_line = lines[0]

    # --- Extract URLs from raw HTML ---
    raw_urls = re.findall(r"https?://[^\s\"'<>]+", html)
    cleaned_urls: list[str] = []
    quiz_url_stripped = quiz_url.rstrip(").,;")

    asset_exts = (
        ".js", ".css", ".png", ".jpg", ".jpeg",
        ".gif", ".ico", ".svg", ".map"
    )

    for u in raw_urls:
        u_clean = u.rstrip(").,;")
        if u_clean == quiz_url_stripped:
            continue
        if any(u_clean.lower().endswith(ext) for ext in asset_exts):
            continue
        if u_clean not in cleaned_urls:
            cleaned_urls.append(u_clean)

    logger.info("[_extract_question_and_submit_url] Candidate URLs: %r", cleaned_urls)

    if cleaned_urls:
        preferred = [
            u for u in cleaned_urls
            if any(key in u.lower() for key in ("submit", "answer", "quiz"))
        ]
        candidate = preferred[0] if preferred else cleaned_urls[0]
        parsed_cand = urlparse(candidate)

        if parsed_cand.path in ("", "/"):
            submit_url = f"{parsed_cand.scheme}://{parsed_cand.netloc}/submit"
        else:
            submit_url = candidate
    else:
        parsed = urlparse(quiz_url)
        submit_url = f"{parsed.scheme}://{parsed.netloc}/submit"
        logger.warning(
            "[_extract_question_and_submit_url] No URLs found in HTML; "
            "falling back to %s",
            submit_url,
        )

    quiz_context = "\n".join(lines[:120])  # give LLM a bit more context
    return quiz_context, submit_url


def _normalise_answer_from_envelope(envelope: dict) -> Optional[object]:
    """
    Try very hard to pull a usable ANSWER out of the script's JSON.
    Returns a value (which may be str/number/bool) or None if we have no idea.
    """
    # 1. Direct 'answer' key
    if "answer" in envelope:
        return envelope["answer"]

    # 2. Common alternatives the model might use
    for key in ("result", "value", "output", "answer_value", "data"):
        if key in envelope:
            logger.info(
                "[solve_quiz] Using fallback key '%s' as answer: %r",
                key,
                envelope[key],
            )
            return envelope[key]

    # 3. Nested 'answer' inside another dict value
    for v in envelope.values():
        if isinstance(v, dict) and "answer" in v:
            logger.info(
                "[solve_quiz] Found nested 'answer' inside envelope: %r",
                v["answer"],
            )
            return v["answer"]

    return None


def _make_answer_json_safe(answer: object) -> object:
    """
    Ensure 'answer' we send to /submit is JSON-serialisable AND not a bare object.

    - If answer is dict/list → convert to JSON string.
    - If answer is bytes → decode to utf-8 string.
    - If answer is anything else JSON-serialisable → leave as is.
    """
    # dict/list → send as JSON string
    if isinstance(answer, (dict, list)):
        try:
            return json.dumps(answer, separators=(",", ":"))
        except Exception:
            # Fallback: string representation
            return str(answer)

    # bytes → decode to string
    if isinstance(answer, (bytes, bytearray)):
        return answer.decode("utf-8", errors="ignore")

    # plain scalar is fine
    return answer


def solve_quiz(email: str, secret: str, start_url: str, deadline_ts: float) -> None:
    """
    Core loop:
    - While we have a current_url and time left:
      - Render page with Playwright
      - Parse quiz context + submit URL (fallback)
      - Ask Gemini to generate a Python script that computes ANSWER and prints JSON
      - Run script, parse its stdout JSON to get ANSWER (+ optional submit_url override)
      - Submit ANSWER ourselves to submit_url
      - Follow next URL if provided and time remains
    """
    current_url: Optional[str] = start_url

    logger.info(
        "[solve_quiz] Starting for email=%s, start_url=%s, deadline_ts=%s",
        email,
        start_url,
        deadline_ts,
    )

    while current_url and time.time() < deadline_ts - 30:
        logger.info("[solve_quiz] Current URL: %s", current_url)

        # 1. Render quiz page
        try:
            html = fetch_rendered_html(current_url)
            logger.info(
                "[solve_quiz] Rendered %s (length=%s bytes)",
                current_url,
                len(html),
            )
        except Exception as e:
            logger.exception("[solve_quiz] Error rendering %s: %s", current_url, e)
            break

        # 2. Extract quiz context and a fallback submit URL
        try:
            quiz_context, submit_url = _extract_question_and_submit_url(html, current_url)
            logger.info(
                "[solve_quiz] Parsed fallback submit_url=%s from page", submit_url
            )
        except Exception as e:
            logger.exception(
                "[solve_quiz] Failed to parse quiz page %s: %s", current_url, e
            )
            break

        # 3. Ask Gemini to generate a solver script (that only prints {"answer": ...})
        try:
            script_code = generate_solver_script(
                quiz_context=quiz_context,
                quiz_url=current_url,
                submit_url=submit_url,
                email=email,
                secret=secret,
            )
            logger.info(
                "[solve_quiz] Received script from Gemini (length=%s chars)",
                len(script_code),
            )
        except Exception as e:
            logger.exception("[solve_quiz] Error calling Gemini: %s", e)
            break

        # 4. Run script in a subprocess and get ANSWER JSON
        result = run_script(script_code)
        logger.info(
            "[solve_quiz] Script returncode=%s, stderr=%s",
            result["returncode"],
            result["stderr"],
        )
        logger.info(
            "[solve_quiz] Script raw stdout (truncated): %r",
            result["stdout"][:400],
        )

        envelope = result.get("response")
        if not isinstance(envelope, dict):
            logger.warning(
                "[solve_quiz] Script did not return valid JSON; stdout=%r",
                result["stdout"],
            )
            break

        logger.info("[solve_quiz] Script envelope: %r", envelope)

        # If the script itself already called /submit and printed the server response,
        # the envelope will look like {"correct": ..., "url": ..., "reason": ...}.
        if "correct" in envelope and "url" in envelope:
            logger.info(
                "[solve_quiz] Envelope looks like server response (script submitted). "
                "Treating as submission."
            )
            submission = envelope
        else:
            # 5. Derive an ANSWER from the envelope
            answer = _normalise_answer_from_envelope(envelope)
            if answer is None:
                logger.warning(
                    "[solve_quiz] Script did not produce an 'answer' or fallback keys; "
                    "defaulting answer to empty string for submission."
                )
                answer = ""

            # Optional submit_url override from script
            override_submit = envelope.get("submit_url") or envelope.get("submission_url")
            if isinstance(override_submit, str) and override_submit.startswith("http"):
                logger.info(
                    "[solve_quiz] Overriding submit_url from script output: %s",
                    override_submit,
                )
                submit_url = override_submit

            # Make 'answer' safe for JSON
            answer = _make_answer_json_safe(answer)
            logger.info("[solve_quiz] Using normalised answer=%r for submission", answer)

            # 6. Submit ANSWER ourselves to submit_url
            try:
                payload = {
                    "email": email,
                    "secret": secret,
                    "url": current_url,
                    "answer": answer,
                }
                logger.info(
                    "[solve_quiz] Submitting payload to %s: %r", submit_url, payload
                )

                with httpx.Client(timeout=30.0) as client:
                    resp = client.post(submit_url, json=payload)

                status = resp.status_code
                raw_text = resp.text
                logger.info(
                    "[solve_quiz] Raw submission response: status=%s, text=%r",
                    status,
                    raw_text[:300],
                )

                try:
                    submission = resp.json()
                except ValueError:
                    submission = {
                        "correct": False,
                        "url": None,
                        "reason": (
                            f"Non-JSON response from submit endpoint "
                            f"(status={status}): {raw_text[:200]}"
                        ),
                    }

                logger.info(
                    "[solve_quiz] Submission response JSON (parsed): %r", submission
                )

            except Exception as e:
                logger.exception("[solve_quiz] Error submitting answer: %s", e)
                break

        # 7. Handle server response and possible next URL
        correct = submission.get("correct")
        next_url = submission.get("url")

        if correct is True:
            logger.info("[solve_quiz] Answer marked correct.")
            if isinstance(next_url, str) and next_url:
                logger.info("[solve_quiz] Moving to next URL: %s", next_url)
                current_url = next_url
                continue
            else:
                logger.info("[solve_quiz] No next URL provided. Quiz complete.")
                break
        else:
            logger.warning(
                "[solve_quiz] Answer incorrect or not marked correct. Submission: %r",
                submission,
            )
            if isinstance(next_url, str) and next_url and time.time() < deadline_ts - 60:
                logger.info(
                    "[solve_quiz] Trying next URL despite incorrect answer: %s",
                    next_url,
                )
                current_url = next_url
                continue
            else:
                logger.info("[solve_quiz] Stopping after incorrect answer.")
                break

    logger.info("[solve_quiz] Exiting for email=%s", email)
