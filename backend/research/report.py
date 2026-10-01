import os

from dotenv import load_dotenv
from google import genai

load_dotenv()


def synthesize_report(
    question: str,
    findings: list[dict],
) -> str:
    """Combine research findings into a final answer."""

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    findings_text = "\n\n".join(
        f"Subquestion: {item.get('subquestion', '')}\n"
        f"Finding: {item.get('finding', '')}"
        for item in findings
    )

    prompt = f"""
You are the final research synthesis agent.

Answer the user's original question using ONLY the research findings
provided below.

Original question:
{question}

Research findings:
{findings_text}

Instructions:
- Directly answer the original question.
- Combine the relevant findings into one coherent answer.
- Do not invent facts or information.
- If the findings are insufficient, clearly say so.
- If findings conflict, clearly mention the conflict.
- Keep the answer factual and well structured.
- Preserve relevant source URLs mentioned in the findings.
"""

    response = client.models.generate_content(
        model="gemini-3.8-flash",
        contents=prompt,
    )

    return response.text