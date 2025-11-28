from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, AnyHttpUrl, ValidationError
from dotenv import load_dotenv
import os
import time
import logging

from .solver import solve_quiz

# Load environment variables from .env
load_dotenv()

EXPECTED_SECRET = os.environ.get("EXPECTED_SECRET")

app = FastAPI()

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class QuizRequest(BaseModel):
    email: str
    secret: str
    url: AnyHttpUrl  # strict URL; can be relaxed later if needed

    class Config:
        # Accept and ignore any additional fields in the incoming JSON,
        # as the spec allows "... other fields"
        extra = "ignore"


def process_request(email: str, secret: str, url: str, received_at: float) -> None:
    """
    Background job entrypoint.
    Computes a 3-minute deadline and calls the quiz solver.
    """
    deadline_ts = received_at + 180  # 3 minutes from first POST
    logger.info(
        "[process_request] email=%s, url=%s, deadline_ts=%s",
        email,
        url,
        deadline_ts,
    )
    solve_quiz(email=email, secret=secret, start_url=url, deadline_ts=deadline_ts)


@app.post("/quiz")
async def quiz_endpoint(request: Request, background_tasks: BackgroundTasks):
    # 1. Parse raw JSON
    try:
        raw_body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # 2. Validate payload shape (email, secret, url); ignore extra fields
    try:
        payload = QuizRequest(**raw_body)
    except ValidationError:
        raise HTTPException(status_code=400, detail="Invalid payload")

    # 3. Check server-side secret
    if EXPECTED_SECRET is None:
        raise HTTPException(status_code=500, detail="Server secret not configured")

    if payload.secret != EXPECTED_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    # 4. Schedule background solver and return 200 immediately
    received_at = time.time()
    background_tasks.add_task(
        process_request,
        payload.email,
        payload.secret,
        str(payload.url),
        received_at,
    )

    return {"status": "accepted"}
