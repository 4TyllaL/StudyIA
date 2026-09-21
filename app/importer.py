"""Importação tolerante de questões em JSON, CSV ou Markdown.

Toda função devolve (cards, erros). Um card normalizado é:
    {kind, question, options[list[str]], answer, explanation, tags}
onde `answer` é o índice (string) da correta em quiz, ou o verso em flash.
"""
from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

ALIASES = {
    "question": ["question", "pergunta", "enunciado", "q", "front", "frente", "titulo"],
    "options": ["options", "alternativas", "opcoes", "opções", "choices", "itens"],
    "answer": ["answer", "resposta", "correta", "correct", "gabarito", "verso", "back",
               "answer_index", "indice_correto"],
    "explanation": ["explanation", "explicacao", "explicação", "justificativa",
                    "porque", "por_que", "why", "comentario", "comentário"],
    "tags": ["tags", "tag", "assunto", "assuntos", "topico", "tópico", "materia", "matéria"],
    "kind": ["kind", "tipo", "type"],
}

OPTION_RE = re.compile(r"^\s*(\*?)\s*([a-zA-Z])\s*[\)\.\-]\s+(.*\S)\s*$")
FIELD_RE = re.compile(
    r"^\s*(P|Q|PERGUNTA|QUESTION|FRENTE|R|RESPOSTA|ANSWER|GABARITO|VERSO|"
    r"E|EXPLICACAO|EXPLICAÇÃO|EXPLANATION|T|TAGS|TAG)\s*[:：]\s*(.*)$",
    re.IGNORECASE,
)
FIELD_MAP = {
    "p": "question", "q": "question", "pergunta": "question", "question": "question",
    "frente": "question",
    "r": "answer", "resposta": "answer", "answer": "answer",
    "gabarito": "answer", "verso": "answer",
    "e": "explanation", "explicacao": "explanation", "explicação": "explanation",
    "explanation": "explanation",
    "t": "tags", "tags": "tags", "tag": "tags",
}


def _pick(d: dict, field: str) -> Any:
    lowered = {str(k).strip().lower(): v for k, v in d.items()}
    for alias in ALIASES[field]:
        if alias in lowered and lowered[alias] not in (None, ""):
            return lowered[alias]
    return None


def resolve_answer_index(raw: Any, options: list[str]) -> int | None:
    """Aceita índice 0-based, letra (A/B/C) ou o texto exato da alternativa."""
    if raw is None or not options or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        if 0 <= raw < len(options):
            return raw
        return raw - 1 if 1 <= raw <= len(options) else None

    text = str(raw).strip()
    if not text:
        return None
    if text.isdigit():
        n = int(text)
        if 0 <= n < len(options):
            return n
        return n - 1 if 1 <= n <= len(options) else None
    letter = text.rstrip(").").strip()
    if len(letter) == 1 and letter.isalpha():
        idx = ord(letter.upper()) - ord("A")
        return idx if 0 <= idx < len(options) else None
    for i, opt in enumerate(options):
        if opt.strip().lower() == text.lower():
            return i
    return None


def _normalize(raw: dict, line_ref: str) -> tuple[dict | None, str | None]:
    question = _pick(raw, "question")
    if not question or not str(question).strip():
        return None, f"{line_ref}: sem pergunta"

    options = _pick(raw, "options") or []
    if isinstance(options, str):
        options = [p.strip() for p in re.split(r"\s*[|;]\s*", options) if p.strip()]
    options = [str(o).strip() for o in options if str(o).strip()]

    answer_raw = _pick(raw, "answer")
    explanation = str(_pick(raw, "explanation") or "").strip()
    tags = _pick(raw, "tags") or ""
    if isinstance(tags, list):
        tags = ", ".join(str(t) for t in tags)

    kind_raw = str(_pick(raw, "kind") or ("quiz" if options else "flash")).lower()
    kind = "quiz" if kind_raw.startswith(("q", "m")) else "flash"

    if kind == "quiz":
        if len(options) < 2:
            return None, f"{line_ref}: quiz precisa de ao menos 2 alternativas"
        idx = resolve_answer_index(answer_raw, options)
        if idx is None:
            return None, f"{line_ref}: não identifiquei a alternativa correta ({answer_raw!r})"
        answer = str(idx)
    else:
        if answer_raw is None or not str(answer_raw).strip():
            return None, f"{line_ref}: flashcard sem verso/resposta"
        answer = str(answer_raw).strip()
        options = []

    return {
        "kind": kind,
        "question": str(question).strip(),
        "options": options,
        "answer": answer,
        "explanation": explanation,
        "tags": str(tags).strip(),
    }, None


def parse_json(content: str) -> tuple[list[dict], list[str]]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        return [], [f"JSON inválido: {exc}"]
    if isinstance(data, dict):
        for key in ("cards", "questions", "questoes", "questões", "items", "perguntas"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        return [], ["JSON precisa ser uma lista de questões"]

    cards, errors = [], []
    for i, raw in enumerate(data, 1):
        if not isinstance(raw, dict):
            errors.append(f"item {i}: não é um objeto")
            continue
        card, err = _normalize(raw, f"item {i}")
        if card:
            cards.append(card)
        else:
            errors.append(err)
    return cards, errors


def parse_csv(content: str) -> tuple[list[dict], list[str]]:
    try:
        dialect = csv.Sniffer().sniff(content[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(content), dialect=dialect)
    if not reader.fieldnames:
        return [], ["CSV vazio ou sem cabeçalho"]

    # Colunas soltas de alternativa: a, b, c, d / alt1, alt2, opcao_a...
    opt_cols = [
        col for col in reader.fieldnames
        if col and re.fullmatch(r"(alt(ernativa)?|op[cç][aã]o)?[\s_-]*[a-eA-E1-9]", col.strip(), re.I)
    ]
    cards, errors = [], []
    for i, row in enumerate(reader, 2):
        row = {k: v for k, v in row.items() if k is not None}
        if not any((v or "").strip() for v in row.values()):
            continue
        if not _pick(row, "options") and len(opt_cols) >= 2:
            row["options"] = [row[c] for c in opt_cols if (row.get(c) or "").strip()]
        card, err = _normalize(row, f"linha {i}")
        if card:
            cards.append(card)
        else:
            errors.append(err)
    return cards, errors


def parse_markdown(content: str) -> tuple[list[dict], list[str]]:
    blocks = re.split(
        r"(?m)^\s*(?:-{3,}|={3,}|\*{3,})\s*$|\n\s*\n(?=\s*(?:P|Q|Pergunta|Question|Frente)\s*[:：])",
        content,
    )
    cards, errors = [], []
    for bi, block in enumerate(blocks, 1):
        if not block or not block.strip():
            continue
        raw: dict[str, Any] = {"options": [], "explanation": "", "tags": ""}
        marked_idx = None
        current = None
        for line in block.splitlines():
            if not line.strip():
                current = None
                continue
            m_opt = OPTION_RE.match(line)
            m_field = FIELD_RE.match(line)
            if m_field:
                key = FIELD_MAP.get(m_field.group(1).lower())
                if key:
                    raw[key] = m_field.group(2).strip()
                    current = key
                    continue
            if m_opt:
                star, _letter, text = m_opt.groups()
                if text.rstrip().endswith("*"):
                    star, text = "*", text.rstrip()[:-1].strip()
                if star:
                    marked_idx = len(raw["options"])
                raw["options"].append(text)
                current = None
                continue
            if current:  # continuação de campo multilinha
                raw[current] = f"{raw[current]} {line.strip()}".strip()
            elif not raw.get("question"):
                raw["question"] = line.strip()
                current = "question"
        if not raw.get("question"):
            continue
        if marked_idx is not None:
            raw["answer"] = marked_idx
        card, err = _normalize(raw, f"bloco {bi}")
        if card:
            cards.append(card)
        else:
            errors.append(err)
    return cards, errors


def detect_format(content: str) -> str:
    stripped = content.lstrip()
    if stripped.startswith(("[", "{")):
        return "json"
    first_line = stripped.split("\n", 1)[0]
    if FIELD_RE.match(first_line) or OPTION_RE.match(first_line):
        return "markdown"
    lowered = first_line.lower()
    if any(sep in lowered for sep in (",", ";", "\t")) and any(
        alias in lowered for group in ALIASES.values() for alias in group
    ):
        return "csv"
    return "markdown"


def parse(content: str, fmt: str = "auto") -> tuple[list[dict], list[str]]:
    fmt = (fmt or "auto").lower()
    if fmt == "auto":
        fmt = detect_format(content)
    if fmt == "json":
        return parse_json(content)
    if fmt == "csv":
        return parse_csv(content)
    return parse_markdown(content)
