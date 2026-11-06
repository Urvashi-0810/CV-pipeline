"""
Test helper: patches the Anthropic + Gemini SDKs so tests need no API key / network.

The fake "LLM" looks at which Pydantic model was requested and returns plausible data:
  Candidate        -> a fixed candidate
  JobRequirements  -> a fixed Python-developer rubric
  JobFitLLM        -> reads the numbered requirements from the prompt and marks each
                      'met' if the word appears in the CV part of the prompt, else 'not_met'
                      (so different CVs get different, predictable scores)
"""
import json
import re
from unittest.mock import patch

from anthropic.types import Message, ToolUseBlock, Usage
from google.genai import types as gt

SENT = []

CANDIDATE = {"full_name": "Rahul Sharma", "email": "rahul.sharma@example.com", "phone": "+91 98765 43210",
             "passport_number": "P1234567",
             "skills": ["Python", "AWS", "Python"], "total_experience_years": 10.25,
             "education": [{"degree": "B.E. Computer", "institution": "Univ of Pune", "year": "2016", "score": "72%"}],
             "current_job_title": "Senior Software Engineer", "current_company": "Infosys",
             "date_of_birth": "1994-03-14", "age": None, "certifications": ["AWS SA"],
             "languages": ["English", "Hindi"], "location": "Pune", "summary": "Backend dev."}

REQUIREMENTS = {
    "job_title": "Python Developer", "seniority": "Mid", "min_experience_years": 3, "max_experience_years": None,
    "location": "Pune",
    "requirements": [
        {"criterion": "Python", "importance": "must", "category": "skill", "jd_quote": "Python developer"},
        {"criterion": "Django", "importance": "must", "category": "skill", "jd_quote": "with Django"},
        {"criterion": "AWS", "importance": "must", "category": "skill", "jd_quote": "and AWS"},
        {"criterion": "Kubernetes", "importance": "nice", "category": "skill", "jd_quote": "Kubernetes nice to have"},
        {"criterion": "3+ years backend", "importance": "must", "category": "experience", "jd_quote": "3+ years backend"},
    ],
    "key_responsibilities": [],
}

# Things a real LLM tends to add that are NOT in the test JD - the grounding check must drop them
UNGROUNDED = [
    {"criterion": "Docker", "importance": "nice", "category": "skill", "jd_quote": "containerisation with Docker"},
    {"criterion": "Flask", "importance": "nice", "category": "skill", "jd_quote": "Python developer"},
    {"criterion": "Strong communication skills", "importance": "nice", "category": "other",
     "jd_quote": "Python developer"},
]

RUBRIC_LINE = re.compile(r"^\d+\. \[(?:MUST|NICE)\] \(\w+\) (.+)$", re.M)


def _prompt_text(obj) -> str:
    """Collect every string inside the (nested) messages structure."""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return "\n".join(_prompt_text(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return "\n".join(_prompt_text(v) for v in obj)
    return ""


def fake_for(model_name: str, prompt: str) -> dict:
    if model_name == "Ping":
        return {"answer": "pong"}
    if model_name == "JobRequirements":
        return {**REQUIREMENTS, "location": "Bangalore",            # location not in JD -> cleared
                "requirements": REQUIREMENTS["requirements"] + UNGROUNDED}
    if model_name == "JobFitLLM":
        criteria = RUBRIC_LINE.findall(prompt)
        cv = prompt.split("=== FULL CV ===")[-1].lower()
        assess = []
        for c in criteria:
            word = c.split()[0].lower() if not c[0].isdigit() else "backend"
            ok = word in cv
            assess.append({"criterion": c, "status": "met" if ok else "not_met",
                           "evidence": "found in CV" if ok else "No evidence in CV"})
        return {"assessments": assess, "overall_score": 70, "strengths": ["Python"], "gaps": ["K8s"],
                "reasoning": "Solid backend profile.", "interview_questions": ["Q1?", "Q2?", "Q3?"]}
    return CANDIDATE


def fake_anthropic_create(self, **kw):
    SENT.append(("claude", kw))
    tools = kw.get("tools") or []
    name = tools[0]["name"] if tools else "Candidate"
    data = fake_for(name, _prompt_text(kw.get("messages")))
    return Message(id="m", type="message", role="assistant", model=kw["model"], stop_reason="tool_use",
                   content=[ToolUseBlock(id="t", type="tool_use", name=name, input=data)],
                   usage=Usage(input_tokens=1, output_tokens=1))


def fake_genai_generate(self, **kw):
    SENT.append(("gemini", kw))
    rs = getattr(kw.get("config"), "response_schema", None)
    name = getattr(rs, "__name__", "Candidate")
    text = " ".join(p.text or "" for c in kw.get("contents", []) for p in (c.parts or []))
    data = fake_for(name, text)
    return gt.GenerateContentResponse(candidates=[gt.Candidate(
        content=gt.Content(role="model", parts=[gt.Part(text=json.dumps(data))]), finish_reason="STOP")])


def start():
    patch("anthropic.resources.messages.Messages.create", fake_anthropic_create).start()
    patch("google.genai.models.Models.generate_content", fake_genai_generate).start()
