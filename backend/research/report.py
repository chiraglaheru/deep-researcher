import datetime
from .llm import ask

SYSTEM = """You write research reports using ONLY the numbered evidence provided. Cite as [n].
Never state a claim the evidence does not support. Structure (markdown):
## Short answer  (direct recommendation, 2-4 sentences)
## Key findings  (bullets, each cited)
## Where sources disagree  (use the contradictions given; say if none)
## Freshness & caveats  (flag stale/undated sources, thin evidence, missing areas)
Today's date is {today}."""


def write_report(question, collector, contradictions):
    user = (f"Question: {question}\n\nEVIDENCE:\n{collector.context()}\n\n"
            f"CONTRADICTIONS FOUND:\n{contradictions}")
    return ask(SYSTEM.replace("{today}", str(datetime.date.today())), user, role="synth")
