# Q_solver: Autonomous LLM Quiz Solver

**Q_solver** is a robust, autonomous API service designed to solve data-related quizzes. It receives a task URL, inspects the content using a headless browser, leverages a Large Language Model (Gemini) to write and execute a solution script, and submits the answer—all within a strict time limit.

## Features

* **Autonomous Orchestration**: Automatically renders JavaScript-heavy pages, extracts task context, and identifies submission endpoints.
* **Robust LLM Integration**: Uses **Google Gemini 1.5 Flash** (with fallback to Pro) to generate Python solution scripts.
* **Sandboxed Execution**: Generated scripts are executed in a controlled subprocess to compute answers without crashing the main application.
* **Type Safety**: Automatically handles API constraints (e.g., converting complex JSON objects to strings to prevent `D1_TYPE_ERROR`).
* **Fault Tolerance**:
    * Retries on network failures or API timeouts.
    * Defaults to safe fallback values if the LLM fails to produce an answer, ensuring the submission pipeline never hangs.
    * Handles recursive quiz chains (following `next_url` instructions).
* **Security**: Verifies request secrets before processing to prevent unauthorized usage.

## Tech Stack

* **Language**: Python 3.10+
* **Web Framework**: FastAPI
* **Browser Automation**: Playwright (Headless Chromium)
* **LLM Provider**: Google Gemini API (`generativelanguage.googleapis.com`)
* **Data Processing**: Pandas, NumPy, BeautifulSoup4
* **HTTP Client**: HTTPX (Async & Sync)

## Project Structure

```

Q\_solver/
├── app/
│   ├── **init**.py
│   ├── main.py           \# FastAPI entry point & validation
│   ├── solver.py         \# Core orchestration logic (The "Foreman")
│   ├── browser.py        \# Playwright headless browser handler
│   ├── llm\_client.py     \# Gemini API client with prompt engineering
│   └── script\_runner.py  \# Subprocess executor for generated scripts
├── .env                  \# Environment variables (Secrets)
├── .gitignore
├── requirements.txt
└── README.md

````

## Setup & Installation

1.  **Clone the repository:**
    ```bash
    git clone [https://github.com/Sanchita0812/Q_Solver](https://github.com/Sanchita0812/Q_Solver)
    cd Q_solver
    ```

2.  **Create a virtual environment:**
    ```bash
    python3 -m venv .venv
    source .venv/bin/activate  # On Windows: .venv\Scripts\activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install fastapi "uvicorn[standard]" httpx pydantic python-dotenv playwright beautifulsoup4 pandas numpy matplotlib networkx
    ```

4.  **Install Playwright browsers:**
    ```bash
    playwright install chromium
    ```

5.  **Configure Environment:**
    Create a `.env` file in the root directory:
    ```ini
    # The registered secret
    EXPECTED_SECRET=your_secret

    # Your Google Gemini API Key
    GEMINI_API_KEY=your_actual_api_key_here
    ```

## Usage

### 1. Start the Server
Run the FastAPI server using Uvicorn:
```bash
uvicorn app.main:app --reload
````

The server will start at `http://127.0.0.1:8000`.

### 2\. Trigger a Quiz Task

You can trigger the solver using `curl` or Postman.

**Example Request:**

```bash
curl -X POST "[http://127.0.0.1:8000/quiz](http://127.0.0.1:8000/quiz)" \
     -H "Content-Type: application/json" \
     -d '{
           "email": "your_email@example.com",
           "secret": "your_secret",
           "url": "[https://tds-llm-analysis.s-anand.net/demo](https://tds-llm-analysis.s-anand.net/demo)"
         }'
```

### 3\. Check Logs

The application logs detailed progress to the console, including:

  * Page rendering status.
  * Generated Python scripts (truncated).
  * Script execution results (stdout/stderr).
  * Submission responses and next task URLs.

## Architecture & Design Choices

1.  **The "Orchestrator" Pattern**:
    Instead of letting the LLM-generated script submit the answer directly (which is prone to network errors or credential leaks), the **Main Application (`solver.py`)** acts as the orchestrator. It extracts the answer from the script's `stdout` and performs the HTTP submission itself.

2.  **Defensive JSON Parsing**:
    The system is designed to be extremely forgiving. If the LLM generates a nested dictionary but the quiz server expects a string, the orchestrator automatically serializes the data to JSON string format. If the LLM produces no answer, it defaults to an empty string `""` to ensure the API contract is met and we receive a descriptive error message rather than a timeout.

3.  **Heuristic URL Extraction**:
    The system uses a multi-layered approach to find the submission URL:

    1.  Scans visible text for "Post your answer to..." instructions.
    2.  Scans HTML for links containing "submit".
    3.  Falls back to a standard `host + /submit` pattern.

## License

This project is licensed under the MIT License.

