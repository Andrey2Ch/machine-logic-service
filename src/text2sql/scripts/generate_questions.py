import os
import re
import json
import asyncio
import hashlib
from pathlib import Path
import httpx

"""
Заполняет вопросы RU/EN для собранных SQL шардов JSONL.

Вход:  knowledge/harvest/*/pairs/(select|dml)/examples-*.jsonl
Выход: knowledge/harvest/*/pairs/(select|dml)/examples-with-questions-*.jsonl

Пропускает записи, у которых уже есть question_ru и/или question_en.
"""

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
# Поддержка псевдонимов для автоматического использования последней версии
_raw_model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
if _raw_model == "opus":
    MODEL = "claude-opus-4-1-20250805"
elif _raw_model == "sonnet":
    MODEL = "claude-sonnet-4-20250514"  # Последняя версия Sonnet
else:
    MODEL = _raw_model
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

BASE = Path(__file__).resolve().parents[1] / "knowledge" / "harvest"


def make_ru_prompt(sql: str) -> str:
    return (
        "Ты помощник Text2SQL. Для данного SQL напиши короткий понятный пользовательский вопрос на русском, "
        "который этот SQL отвечает. Без пояснений, только вопрос.\nSQL:\n" + sql
    )


def make_en_prompt(sql: str) -> str:
    return (
        "You are a Text2SQL assistant. For the given SQL, write a short, clear user question in English "
        "that this SQL answers. No explanations, only the question.\nSQL:\n" + sql
    )


async def ask_claude(client: httpx.AsyncClient, prompt: str) -> str:
    if not API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    payload = {
        "model": MODEL,
        "max_tokens": 128,
        "temperature": 0.0,
        "system": "Return only the question text.",
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {"x-api-key": API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    r = await client.post(ANTHROPIC_API_URL, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    text = "".join(part.get("text", "") for part in data.get("content", []) if part.get("type") == "text").strip()
    return re.sub(r"\s+", " ", text)


def make_he_prompt(sql: str) -> str:
    return (
        "אתה עוזר Text2SQL. עבור SQL הנתון, כתוב שאלה קצרה וברורה בעברית "
        "שהשאילתה עונה עליה. ללא הסברים, רק את השאלה.\nSQL:\n" + sql
    )


async def process_shard(client: httpx.AsyncClient, shard_path: Path):
    out_path = shard_path.with_name(shard_path.stem.replace("examples-", "examples-with-questions-") + shard_path.suffix)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    gen_he = os.environ.get("TEXT2SQL_GEN_HE", "0").lower() in {"1", "true", "yes"}

    with shard_path.open("r", encoding="utf-8") as fin, out_path.open("w", encoding="utf-8") as fout:
        for line in fin:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            sql = (rec.get("sql") or "").strip()
            if not sql:
                continue

            already_have = rec.get("question_ru") and rec.get("question_en") and (rec.get("question_he") if gen_he else True)
            if already_have:
                fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                continue

            try:
                if not rec.get("question_ru"):
                    rec["question_ru"] = await ask_claude(client, make_ru_prompt(sql))
                if not rec.get("question_en"):
                    rec["question_en"] = await ask_claude(client, make_en_prompt(sql))
                if gen_he and not rec.get("question_he"):
                    rec["question_he"] = await ask_claude(client, make_he_prompt(sql))
                if not rec.get("language"):
                    rec["language"] = "multi"
            except Exception as e:
                rec.setdefault("_gen_error", str(e))

            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")


def find_shards() -> list[Path]:
    shards: list[Path] = []
    if not BASE.exists():
        return shards
    for project_dir in BASE.iterdir():
        pairs = project_dir / "pairs"
        for kind in ("select", "dml"):
            d = pairs / kind
            if d.exists():
                shards.extend(sorted(d.glob("examples-*.jsonl")))
    return shards


async def main():
    shards = find_shards()
    async with httpx.AsyncClient() as client:
        for shard in shards:
            print(f"Processing {shard}")
            await process_shard(client, shard)
    print("Done")


if __name__ == "__main__":
    asyncio.run(main())


