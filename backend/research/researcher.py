import os

from dotenv import load_dotenv
from google import genai

from backend.evidence.collector import collect_evidence

load_dotenv()


def research_subquestion(
    question: str,
    search_results: list[dict],
) -> str:
    """Analyze collected search evidence and produce a research finding."""

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    evidence = collect_evidence(search_results)

    evidence_text = "\n\n".join(
        f"Title: {item.get('title', '')}\n"
        f"URL: {item.get('url', '')}\n"
        f"Snippet: {item.get('snippet', '')}\n"
        f"Source: {item.get('source', '')}\n"
        f"Date: {item.get('date', '')}"
        for item in evidence
    )

    prompt = f"""
You are a research agent.

Research the following subquestion using ONLY the evidence provided.

Subquestion:
{question}

Evidence:
{evidence_text}

Instructions:
- Answer the subquestion using only the evidence.
- Do not invent facts.
- If the evidence is insufficient, say so.
- Keep the answer concise and factual.
- Mention relevant source URLs when making claims.
"""

    response = client.models.generate_content(
        model="gemini-3.8-flash",
        contents=prompt,
    )

    return response.text