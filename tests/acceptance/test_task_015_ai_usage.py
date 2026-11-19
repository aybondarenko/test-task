"""TASK-015: README: note on AI usage."""

from __future__ import annotations

import re

import pytest
from acc_support import PROJECT_ROOT, md_section

TOOLS = r"Claude|ChatGPT|GPT-?\d|Copilot|Cursor|Gemini|Codex|Aider|Windsurf|Cody|Tabnine|Llama|Mistral|DeepSeek"


@pytest.mark.criterion("TASK-015:C1")
@pytest.mark.req("DEL-05", "AI-02", "AI-03", "AI-04")
def test_ai_usage_section():
    text = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    ai = md_section(text, "AI usage")
    assert ai is not None, "no ## AI usage section"
    subs = {}
    for title in ("Tools", "Where AI was used", "What worked", "What did not work"):
        body = md_section(ai, title, level=3)
        assert body is not None, f"no ### {title} subsection"
        assert body.strip(), f"subsection ### {title} is empty"
        subs[title] = body
    assert re.search(TOOLS, subs["Tools"]), "### Tools does not name a specific tool"
