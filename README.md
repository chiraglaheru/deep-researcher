# Deep Researcher

An agentic research system that takes a research question, breaks it into smaller subquestions, searches for relevant information, collects evidence, generates findings, and synthesizes the results into a final report.

## Overview

Deep Researcher is designed as a multi-stage research pipeline:

```text
Research Question
       ↓
     Planner
       ↓
   Subquestions
       ↓
 Search Orchestrator
       ↓
 ┌─────┼─────┐
 ↓     ↓     ↓
Google GitHub News
 └─────┼─────┘
       ↓
Evidence Collection
       ↓
Researcher Agent
       ↓
Subquestion Findings
       ↓
Report Synthesis
       ↓
   Final Report
```

The system is built with a Python/FastAPI backend and a lightweight frontend.

---

## Features

* Breaks a research question into multiple subquestions.
* Routes subquestions to appropriate search tools.
* Supports:

  * Google search through SerpApi
  * GitHub repository search
  * Google News through SerpApi
* Collects and normalizes research evidence.
* Deduplicates evidence and research results.
* Uses Gemini models for research and report generation.
* Handles individual subquestion failures without stopping the entire research process.
* Preserves successful findings when another subquestion fails.
* Provides a simple browser-based research interface.
* Includes automated tests for the research pipeline.

---

## Project Structure

```text
deep-researcher/
│
├── backend/
│   ├── main.py
│   │
│   ├── tools/
│   │   ├── serpapi.py
│   │   ├── github.py
│   │   ├── news.py
│   │   └── __init__.py
│   │
│   ├── evidence/
│   │   └── collector.py
│   │
│   └── research/
│       ├── planner.py
│       ├── searcher.py
│       ├── researcher.py
│       ├── collector.py
│       └── report.py
│
├── frontend/
│   ├── index.html
│   ├── app.js
│   └── style.css
│
├── tests/
│   ├── test_searcher.py
│   ├── test_research_collector.py
│   ├── test_pipeline_errors.py
│   ├── test_research_api.py
│   └── ...
│
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

---

## Tech Stack

### Backend

* Python
* FastAPI
* Uvicorn
* Pydantic

### AI

* Google Gemini API
* `google-genai`

### Search

* SerpApi
* GitHub REST API
* Google News through SerpApi

### Frontend

* HTML
* CSS
* JavaScript

### Testing

* pytest
* FastAPI TestClient

---

## Requirements

You need:

* Python 3.10+
* A Gemini API key
* A SerpApi API key

A GitHub token is optional but can be used for authenticated GitHub API requests.

---

## Setup

Clone the repository:

```bash
git clone <repository-url>
cd deep-researcher
```

Create a virtual environment:

```bash
python3 -m venv venv
```

Activate it:

```bash
source venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

For development and testing:

```bash
pip install -r requirements-dev.txt
```

---

## Environment Variables

Create a `.env` file in the project root:

```env
GEMINI_API_KEY=your_gemini_api_key
SERPAPI_KEY=your_serpapi_key
GITHUB_TOKEN=your_github_token
```

`GITHUB_TOKEN` is optional.

Do not commit `.env` or API keys to the repository.

---

## Running the Backend

From the project root:

```bash
source venv/bin/activate
uvicorn backend.main:app --reload
```

The API will be available at:

```text
http://127.0.0.1:8000
```

Health check:

```text
GET /health
```

---

## Running the Frontend

In a second terminal:

```bash
cd ~/Documents/deep-researcher
python3 -m http.server 5500 --directory frontend
```

Open:

```text
http://127.0.0.1:5500
```

The frontend sends research requests to the backend API.

---

## Research API

### `POST /research`

Accepts a research question and runs the research pipeline.

Example request:

```json
{
  "question": "What is FastAPI and why is it useful?"
}
```

The response contains:

```json
{
  "question": "What is FastAPI and why is it useful?",
  "plan": {
    "subquestions": [
      "What is FastAPI?",
      "What are the main features of FastAPI?",
      "Why is FastAPI useful?"
    ]
  },
  "report": "Final synthesized report...",
  "findings": [
    {
      "subquestion": "What is FastAPI?",
      "finding": "..."
    }
  ]
}
```

If report synthesis fails because of a documented Gemini API error, the successful findings can still be returned:

```json
{
  "question": "...",
  "plan": {
    "subquestions": []
  },
  "report": null,
  "findings": [],
  "report_error": "..."
}
```

---

## Research Pipeline

### 1. Planner

The planner sends the research question to Gemini and generates a structured list of subquestions.

The planner validates the Gemini response and requires:

* Valid JSON
* A JSON object
* A `subquestions` field
* `subquestions` to be a list
* Every subquestion to be a non-empty string

Invalid planner responses are rejected instead of being silently repaired.

### 2. Search Orchestrator

Each subquestion is routed to the appropriate search tools.

Google search is used as the general search source.

GitHub search can be added for questions involving:

* repositories
* libraries
* frameworks
* software
* implementations
* code

News search can be added for questions involving:

* recent events
* latest information
* announcements
* releases
* news

Comparison-style questions can use multiple sources.

### 3. Evidence Collection

Search results are normalized into a consistent structure:

```json
{
  "title": "...",
  "url": "...",
  "snippet": "...",
  "source": "...",
  "date": "..."
}
```

Duplicate URLs are removed while preserving the first occurrence.

### 4. Researcher

The researcher agent uses the collected evidence to generate a finding for each subquestion.

Individual documented search or Gemini API failures are recorded against the affected subquestion rather than automatically destroying the complete research run.

### 5. Report Synthesis

Successful findings are passed to the report synthesizer.

The synthesizer uses Gemini to produce the final research report.

If report synthesis encounters a documented Gemini API failure, successful findings are preserved and the response records the report error.

---

## Error Handling

The pipeline is designed to distinguish expected external-service failures from programming errors.

For example:

```text
Subquestion 1 → finding
Subquestion 2 → search error
Subquestion 3 → finding
```

The research process can continue with the successful subquestions.

Unexpected programming errors are not silently swallowed. This makes genuine bugs visible during development.

---

## Testing

Run the complete test suite:

```bash
pytest -q
```

The project includes tests for:

* Planner validation
* Search routing
* Search-tool failures
* Researcher failures
* Multiple subquestion failures
* Evidence collection
* Result collection
* API behavior
* Report synthesis failures
* Gemini API errors
* Frontend/API integration behavior

---

## Development Workflow

Create a feature branch before making changes:

```bash
git switch -c feature/your-feature
```

Run tests:

```bash
pytest -q
```

Check for whitespace errors:

```bash
git diff --check
```

Commit your changes:

```bash
git add .
git commit -m "Describe your change"
```

Push the branch:

```bash
git push -u origin feature/your-feature
```

Open a Pull Request for review.

---

## API Flow

A simplified view of the backend flow:

```text
POST /research
      │
      ▼
 create_research_plan()
      │
      ▼
  subquestions
      │
      ├──────────────┐
      ▼              ▼
 search_subquestion  ...
      │
      ▼
 research_subquestion()
      │
      ▼
   findings
      │
      ▼
 synthesize_report()
      │
      ▼
 final response
```

---

## Current Development Notes

The project is under active development.

The frontend and backend are currently run separately during local development:

```text
Frontend: http://127.0.0.1:5500
Backend:  http://127.0.0.1:8000
```

Because the frontend and backend use different origins during local development, the backend provides CORS configuration for the frontend.

---

## Contributing

1. Create a feature branch.
2. Make focused changes.
3. Add or update tests.
4. Run the complete test suite.
5. Push the branch.
6. Open a Pull Request.
7. Address review feedback.

Do not commit API keys or other secrets.

---


