import time
import logging

import httpx

logger = logging.getLogger(__name__)


def solve_quiz(email: str, secret: str, start_url: str, deadline_ts: float) -> None:
    """
    Minimal solver skeleton.

    For now:
    - Maintain a loop with a time budget.
    - Fetch the current_url once with httpx.get().
    - Log what we're doing and then break.
    """
    current_url = start_url

    logger.info(
        "[solve_quiz] Starting for email=%s, start_url=%s, deadline_ts=%s",
        email,
        start_url,
        deadline_ts,
    )

    while current_url and time.time() < deadline_ts - 30:
        logger.info("[solve_quiz] Current URL: %s", current_url)

        try:
            resp = httpx.get(current_url, timeout=30.0)
            logger.info(
                "[solve_quiz] Fetched %s with status %s, length=%s bytes",
                current_url,
                resp.status_code,
                len(resp.content),
            )
        except Exception as e:
            logger.exception("[solve_quiz] Error fetching %s: %s", current_url, e)

        # Day 1 stub: we don't actually solve the quiz yet.
        break

    logger.info("[solve_quiz] Exiting for email=%s", email)
