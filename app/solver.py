import time
import logging
import httpx
import json
import re
from urllib.parse import urlparse
from bs4 import BeautifulSoup

from .browser import fetch_rendered_html
from .llm_client import generate_solver_script
from .script_runner import run_script

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _extract_question_and_submit_url(html: str, current_url: str):
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n")

    # Simple heuristic for submit URL:
    # 1. Look for "Post your answer to..." in text
    # 2. Fallback to host + /submit

    submit_url = None
    url_pattern = re.compile(r"https?://\S+")

    for line in text.splitlines():
        if "Post your answer to" in line:
            match = url_pattern.search(line)
            if match:
                submit_url = match.group(0).rstrip(".,)")
                break

    if not submit_url:
        parsed = urlparse(current_url)
        submit_url = f"{parsed.scheme}://{parsed.netloc}/submit"

    # Limit context to avoid over-long prompts
    return text[:6000], submit_url


def _normalise_answer(answer):
    """
    Make sure 'answer' is something safe and JSON-serialisable for the quiz server.
    """
    # None or string "none"/"" → treat as no usable answer
    if answer is None:
        return None
    if isinstance(answer, str) and answer.strip().lower() in ("none", ""):
        return None

    # dict/list → send as JSON string
    if isinstance(answer, (dict, list)):
        try:
            return json.dumps(answer, separators=(",", ":"))
        except Exception:
            return str(answer)

    # bytes → decode
    if isinstance(answer, (bytes, bytearray)):
        return answer.decode("utf-8", errors="ignore")

    # numbers / bool / normal strings are fine
    return answer


def solve_quiz(email: str, secret: str, start_url: str, deadline_ts: float):
    current_url = start_url

    while current_url and time.time() < deadline_ts - 10:
        logger.info(f"[solve_quiz] Solving: {current_url}")

        # 1. Render quiz page
        try:
            html = fetch_rendered_html(current_url)
            logger.info(
                "[solve_quiz] Rendered %s (len=%s)",
                current_url,
                len(html),
            )
        except Exception as e:
            logger.error(f"[solve_quiz] Render failed for {current_url}: {e}")
            break

        # 2. Parse context + submit URL
        context, submit_url = _extract_question_and_submit_url(html, current_url)
        logger.info(f"[solve_quiz] Using submit_url=%s", submit_url)

        # 3. Ask Gemini for solver script
        try:
            code = generate_solver_script(context, current_url, submit_url, email, secret)
            logger.info(
                "[solve_quiz] Generated script (%s chars)",
                len(code),
            )
        except Exception as e:
            logger.error(f"[solve_quiz] LLM failed: {e}")
            break

        # 4. Run script
        result = run_script(code)
        logger.info(
            "[solve_quiz] Script returncode=%s, stderr=%r",
            result.get("returncode"),
            result.get("stderr"),
        )
        logger.info(
            "[solve_quiz] Script stdout (truncated): %r",
            (result.get("stdout") or "")[:400],
        )

        response_data = result.get("response") or {}
        logger.info("[solve_quiz] Script response envelope: %r", response_data)

        # If the script itself printed the quiz-server response (because it submitted),
        # we may see keys like 'correct' and 'reason'. In that case we can just treat
        # it as the final submission.
        if "correct" in response_data and "url" in response_data:
            logger.info("[solve_quiz] Detected quiz-server style response in script output.")
            resp_json = response_data
        else:
            raw_answer = response_data.get("answer")

            # Normalise answer
            answer = _normalise_answer(raw_answer)
            if answer is None:
                logger.warning(
                    "[solve_quiz] Script did not compute a usable answer "
                    f"(raw_answer={raw_answer!r}); not submitting."
                )
                break

            # 5. Submit
            payload = {
                "email": email,
                "secret": secret,
                "url": current_url,
                "answer": answer,
            }

            logger.info(f"[solve_quiz] Submitting to {submit_url}: {payload!r}")

            try:
                with httpx.Client(timeout=30) as client:
                    resp = client.post(submit_url, json=payload)
                    resp_json = resp.json()
            except Exception as e:
                logger.error(f"[solve_quiz] Submission failed: {e}")
                break

        logger.info(f"[solve_quiz] Server response: {resp_json!r}")

        # 6. Handle quiz-server response
        if resp_json.get("correct"):
            logger.info("[solve_quiz] Correct answer.")
        else:
            logger.warning(f"[solve_quiz] Incorrect: {resp_json.get('reason')}")

        current_url = resp_json.get("url")  # None if quiz over

    logger.info("[solve_quiz] Exiting for email=%s", email)
