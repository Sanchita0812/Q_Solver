from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, AnyHttpUrl, ValidationError
from dotenv import load_dotenv
import os
import time

from .solver import solve_quiz  

load_dotenv()

EXPECTED_SECRET = os.environ.get("EXPECTED_SECRET")

app = FastAPI()


class QuizRequest(BaseModel):
    email: str
    secret: str
    url: AnyHttpUrl  #strict URL for the time being, relaxations to be done as needed 


def process_request(email: str, secret: str, url: str, received_at: float) -> None:
    """
    Background job entrypoint.
    For now, just logs and calls a stub solver with a time budget.
    """
    deadline_ts = received_at + 180  
    print(f"[process_request] email={email}, url={url}, deadline_ts={deadline_ts}")
    solve_quiz(email=email, secret=secret, start_url=url, deadline_ts=deadline_ts)


@app.post("/quiz")
async def quiz_endpoint(request: Request, background_tasks: BackgroundTasks):
    #parsing json
    try:
        raw_body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    #validating payload shape
    try:
        payload = QuizRequest(**raw_body)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail="Invalid payload")

    #checking secret
    if EXPECTED_SECRET is None:
        raise HTTPException(status_code=500, detail="Server secret not configured")

    if payload.secret != EXPECTED_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    #schedule background solver
    received_at = time.time()
    background_tasks.add_task(
        process_request,
        payload.email,
        payload.secret,
        str(payload.url),
        received_at,
    )

    return {"status": "accepted"}
