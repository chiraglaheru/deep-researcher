import json
import os

from dotenv import load_dotenv
from google import genai

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def _validated_plan_text(raw: str) -> str:
    """Return the Gemini JSON text once the plan shape is verified.

    Raises ValueError with a clear message when the text is not JSON, is not a
    JSON object, or does not hold a list of non-empty subquestion strings. The
    text is never rewritten or repaired, only accepted or rejected.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("Gemini returned an empty response")

    try:
        plan = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"Gemini returned malformed JSON: {exc}") from exc

    if not isinstance(plan, dict):
        raise ValueError(f"plan must be a JSON object, got {type(plan).__name__}")

    if "subquestions" not in plan:
        raise ValueError('plan is missing the required "subquestions" list')

    subquestions = plan["subquestions"]
    if not isinstance(subquestions, list):
        raise ValueError(
            f'"subquestions" must be a list, got {type(subquestions).__name__}'
        )

    for index, item in enumerate(subquestions):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(
                f"subquestion at index {index} must be a non-empty string"
            )

    return raw


def create_research_plan(question: str) -> str:
    prompt = f"""
You are a research planning agent.

Break the user's question into 5-7 specific research
subquestions that need to be investigated.

Do NOT answer the question.

Return ONLY a JSON object in this exact format:

{{
    "subquestions": [
        "question 1",
        "question 2",
        "question 3"
    ]
}}

User question:
{question}
"""

    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=prompt,
    )

    return _validated_plan_text(response.text)
