import time
import logging
import re
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
    - Extract ALL http(s) URLs from text
    - Drop the quiz_url itself
    - Drop obvious static assets (.js, .css, images, etc.)
    - Prefer URLs containing 'submit' or 'answer'
    - Fallback to the first remaining URL
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n")

    lines = [line.strip() for line in text.splitlines() if line.strip()]

    # Optional: detect a question line like "Q834. ..."
    question_line = ""
    for line in lines:
        if re.match(r"^Q\d+[\.:]", line):
            question_line = line
            break
    if not question_line and lines:
        question_line = lines[0]

    # Extract all URLs from text
    all_urls = re.findall(r"https?://[^\s\"'<>]+", text)
    cleaned_urls = []
    quiz_url_stripped = quiz_url.rstrip(").,;")

    asset_exts = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".map")

    for u in all_urls:
        u_clean = u.rstrip(").,;")
        if u_clean == quiz_url_stripped:
            continue
        if any(u_clean.lower().endswith(ext) for ext in asset_exts):
            continue
        if u_clean not in cleaned_urls:
            cleaned_urls.append(u_clean)

    if not cleaned_urls:
        raise RuntimeError("Could not find any candidate submit URLs in quiz page text.")

    # Prefer URLs that look like submission endpoints
    preferred = [
        u for u in cleaned_urls
        if "submit" in u.lower() or "answer" in u.lower() or "quiz" in u.lower()
    ]

    if preferred:
        submit_url = preferred[0]
    else:
        submit_url = cleaned_urls[0]

    # Build a trimmed context for the LLM (first ~80 lines)
    quiz_context = "\n".join(lines[:80])
    return quiz_context, submit_url


def solve_quiz(email: str, secret: str, start_url: str, deadline_ts: float) -> None:
    """
    Core loop:
    - While we have a current_url and time left:
      - Render page with Playwright
      - Parse quiz context + submit URL
      - Ask Gemini to generate Python script
      - Run script, parse submission response
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

        # 2. Extract quiz context and submit URL
        try:
            quiz_context, submit_url = _extract_question_and_submit_url(html, current_url)
            logger.info("[solve_quiz] Parsed submit_url=%s from page", submit_url)
        except Exception as e:
            logger.exception(
                "[solve_quiz] Failed to parse quiz page %s: %s", current_url, e
            )
            break

        # 3. Ask Gemini to generate a solver script
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

        # 4. Run script in a subprocess
        result = run_script(script_code)
        logger.info(
            "[solve_quiz] Script returncode=%s, stderr=%s",
            result["returncode"],
            result["stderr"],
        )

        response = result.get("response")
        if not isinstance(response, dict):
            logger.warning(
                "[solve_quiz] Script did not return valid JSON; stdout=%r",
                result["stdout"],
            )
            break

        logger.info("[solve_quiz] Submission response JSON: %r", response)

        # Expected structure:
        # { "correct": true/false, "url": "next-url-or-null", "reason": ... }

        correct = response.get("correct")
        next_url = response.get("url")

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
                "[solve_quiz] Answer incorrect or not marked correct. Response: %r",
                response,
            )
            # If a new URL is provided, we may jump there (as per spec)
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
