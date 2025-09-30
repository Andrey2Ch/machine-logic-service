import os
import re
from typing import List, Dict, Any, Tuple
import httpx


ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"


class ClaudeText2SQL:
    def __init__(self, model: str = "claude-sonnet-4-20250514", max_tokens: int = 1000, temperature: float = 0.0):
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        # Поддержка псевдонимов для автоматического использования последней версии
        if model == "opus":
            self.model = "claude-opus-4-1-20250805"
        elif model == "sonnet":
            self.model = "claude-sonnet-4-20250514"  # Последняя версия Sonnet
        else:
            self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

    def _build_system_prompt(self) -> str:
        return (
            "You are an expert Text-to-SQL assistant for PostgreSQL. "
            "Return ONLY SQL code block without explanations. Prefer selecting existing columns, avoid hallucinations. "
            "If the request is vague, infer reasonable filters and add LIMIT if missing."
        )

    def _compose_context(self, question: str, schema_docs: str, examples: List[Tuple[str, str]], max_chars: int = 8000) -> str:
        # Pick top-k examples by naive keyword overlap
        q = question.lower()
        scored: List[Tuple[int, Tuple[str, str]]] = []
        for ex_q, ex_sql in examples:
            score = sum(1 for w in re.findall(r"\w+", q) if w in ex_q.lower())
            scored.append((score, (ex_q, ex_sql)))
        scored.sort(reverse=True)
        top = [pair for _, pair in scored[:6]]

        # Debug: print selected examples
        print(f"DEBUG: Question: {question}")
        print(f"DEBUG: Selected examples:")
        for i, (ex_q, ex_sql) in enumerate(top):
            print(f"  {i+1}. Score: {scored[i][0]}, Q: {ex_q}")

        ctx_parts = ["# SCHEMA\n", schema_docs[: max_chars // 2], "\n\n# FEW-SHOT EXAMPLES\n"]
        for ex_q, ex_sql in top:
            ctx_parts.append(f"Q: {ex_q}\nSQL:\n{ex_sql}\n\n")
        return "".join(ctx_parts)[:max_chars]

    async def generate_sql(self, question: str, schema_docs: str, examples: List[Tuple[str, str]]) -> str:
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")

        context = self._compose_context(question, schema_docs, examples)
        system = self._build_system_prompt()
        user = (
            f"Context:\n{context}\n\n"
            f"Task: Generate a valid PostgreSQL SQL for the user's question."
            f"\nUser question (any language): {question}\n"
            "Return ONLY the SQL in a fenced code block."
        )
        
        # Debug: print full context
        print(f"DEBUG: Full context length: {len(context)}")
        print(f"DEBUG: Context preview: {context[:500]}...")
        print(f"DEBUG: User prompt: {user[:500]}...")

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "system": system,
            "messages": [
                {"role": "user", "content": user}
            ],
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(ANTHROPIC_API_URL, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        # Extract text
        content = "".join(part.get("text", "") for part in data.get("content", []) if part.get("type") == "text")
        # Extract SQL from code fence if present
        m = re.search(r"```sql\s*(.*?)```", content, re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return content.strip()

    async def generate_structured_plan(self, question: str, schema_docs: str, examples: List[Tuple[str, str]],
                                       allowed_schema_json: str) -> str:
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")

        context = self._compose_context(question, schema_docs, examples)
        system = (
            "You are a Text-to-SQL planner. Return ONLY valid JSON plan with keys: "
            "tables, joins, select, filters, group_by, order_by, limit. "
            "All table/column choices MUST be picked from ALLOWED_SCHEMA."
        )
        user = (
            f"ALLOWED_SCHEMA (JSON):\n{allowed_schema_json}\n\n"
            f"Context:\n{context[:4000]}\n\n"
            f"Task: Build a minimal correct plan for the user's question.\n"
            f"User question: {question}\n"
            "Return ONLY the JSON (no code fences, no explanations)."
        )

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": 800,
            "temperature": 0.0,
            "system": system,
            "messages": [
                {"role": "user", "content": user}
            ],
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(ANTHROPIC_API_URL, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        content = "".join(part.get("text", "") for part in data.get("content", []) if part.get("type") == "text")
        return content.strip()


