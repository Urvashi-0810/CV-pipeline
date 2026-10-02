"""Test helper: patches the Anthropic + Gemini SDKs so tests need no API key / network."""
import json
from unittest.mock import patch
import anthropic
from anthropic.types import Message, ToolUseBlock, Usage
from google.genai import types as gt

SENT = []
FAKE = {"full_name":"Rahul Sharma","email":"rahul.sharma@example.com","phone":"+91 98765 43210",
 "skills":["Python","AWS","Python"],"total_experience_years":10.25,
 "education":[{"degree":"B.E. Computer","institution":"Univ of Pune","year":"2016","score":"72%"}],
 "current_job_title":"Senior Software Engineer","current_company":"Infosys","date_of_birth":"1994-03-14",
 "age":None,"certifications":["AWS SA"],"languages":["English","Hindi"],"location":"Pune","summary":"Backend dev."}

def _payload(kw):
    # name of the tool instructor asked for decides what to return
    tools = kw.get("tools") or []
    name = tools[0]["name"] if tools else "Candidate"
    return name, ({"answer":"pong"} if name == "Ping" else FAKE)

def fake_anthropic_create(self, **kw):
    SENT.append(("claude", kw))
    name, data = _payload(kw)
    return Message(id="m", type="message", role="assistant", model=kw["model"], stop_reason="tool_use",
        content=[ToolUseBlock(id="t", type="tool_use", name=name, input=data)],
        usage=Usage(input_tokens=1, output_tokens=1))

def fake_genai_generate(self, **kw):
    SENT.append(("gemini", kw))
    rs = getattr(kw.get("config"), "response_schema", None)
    data = {"answer":"pong"} if getattr(rs, "__name__", str(rs)) == "Ping" else FAKE
    return gt.GenerateContentResponse(candidates=[gt.Candidate(content=gt.Content(role="model",
        parts=[gt.Part(text=json.dumps(data))]), finish_reason="STOP")])

def start():
    patch("anthropic.resources.messages.Messages.create", fake_anthropic_create).start()
    patch("google.genai.models.Models.generate_content", fake_genai_generate).start()
