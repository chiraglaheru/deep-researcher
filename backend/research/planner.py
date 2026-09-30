import os


from dotenv import load_dotenv
from google import genai

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


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

    return response.text