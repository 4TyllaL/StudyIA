"""Toda a lógica do StudyIA — baralhos, questões, estudo e estatísticas.

Funções Python comuns: recebem valores simples e devolvem dicionários prontos para a
interface. Quem chama é a ponte em `bridge.py` (a janela fala direto com este módulo,
sem HTTP no meio).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

from . import __version__, ai, cofre, importer
from .db import db, depurar_arquivo, get_setting, now_iso, set_setting
from .srs import SchedState, apply_grade, due_from, humanize, preview


class AppError(Exception):
    """Erro esperado, com mensagem pronta para aparecer na tela."""


# --------------------------------------------------------------------------- helpers

def card_row_to_dict(row: sqlite3.Row, *, with_answer: bool = True) -> dict:
    data = {
        "id": row["id"],
        "deck_id": row["deck_id"],
        "kind": row["kind"],
        "question": row["question"],
        "options": json.loads(row["options"] or "[]"),
        "explanation": row["explanation"] if with_answer else "",
        "tags": row["tags"],
        "source": row["source"],
        "suspended": bool(row["suspended"]),
    }
    if with_answer:
        data["answer"] = row["answer"]
    if "state" in row.keys() and row["state"] is not None:
        data["schedule"] = {
            "state": row["state"],
            "due_at": row["due_at"],
            "interval": humanize(row["interval_min"]),
            "ease": round(row["ease"], 2),
            "reps": row["reps"],
            "lapses": row["lapses"],
        }
    return data


def insert_card(conn: sqlite3.Connection, deck_id: int, card: dict, source: str) -> int:
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO card (deck_id, kind, question, options, answer, explanation, tags,"
        " source, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            deck_id,
            card.get("kind", "quiz"),
            card["question"],
            json.dumps(card.get("options") or [], ensure_ascii=False),
            str(card.get("answer", "")),
            card.get("explanation", ""),
            card.get("tags", ""),
            source,
            ts,
            ts,
        ),
    )
    card_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO schedule (card_id, state, step, ease, interval_min, due_at) "
        "VALUES (?, 'new', 0, 2.5, 0, ?)",
        (card_id, ts),
    )
    return card_id


def resolve_deck(conn: sqlite3.Connection, deck_id: int | None, deck_name: str) -> int:
    if deck_id:
        if not conn.execute("SELECT 1 FROM deck WHERE id = ?", (deck_id,)).fetchone():
            raise AppError("Baralho não encontrado")
        return deck_id
    name = (deck_name or "").strip()
    if not name:
        raise AppError("Escolha um baralho ou informe um nome novo")
    row = conn.execute("SELECT id FROM deck WHERE name = ?", (name,)).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO deck (name, description, created_at) VALUES (?, '', ?)", (name, now_iso())
    )
    return int(cur.lastrowid)


CARD_SELECT = "c.*, s.state, s.step, s.due_at, s.interval_min, s.ease, s.reps, s.lapses"


# --------------------------------------------------------------------------- baralhos

def list_decks() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT d.id, d.name, d.description, d.created_at,
                   COUNT(c.id) AS total,
                   COALESCE(SUM(CASE WHEN c.suspended = 0 AND s.due_at <= ? THEN 1 ELSE 0 END), 0) AS due,
                   COALESCE(SUM(CASE WHEN c.suspended = 0 AND s.state = 'new' THEN 1 ELSE 0 END), 0) AS novos,
                   COALESCE(SUM(CASE WHEN c.suspended = 0 AND s.state IN ('learning','relearning') THEN 1 ELSE 0 END), 0) AS aprendendo,
                   COALESCE(SUM(CASE WHEN c.suspended = 0 AND s.state = 'review' THEN 1 ELSE 0 END), 0) AS maduros
            FROM deck d
            LEFT JOIN card c ON c.deck_id = d.id
            LEFT JOIN schedule s ON s.card_id = c.id
            GROUP BY d.id
            ORDER BY d.name COLLATE NOCASE
            """,
            (now_iso(),),
        ).fetchall()
    return [dict(r) for r in rows]


def create_deck(name: str, description: str = "") -> dict:
    name = (name or "").strip()
    if not name:
        raise AppError("Dê um nome ao baralho")
    with db() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO deck (name, description, created_at) VALUES (?,?,?)",
                (name, (description or "").strip(), now_iso()),
            )
        except sqlite3.IntegrityError:
            raise AppError("Já existe um baralho com esse nome")
        return {"id": cur.lastrowid, "name": name}


def update_deck(deck_id: int, name: str, description: str = "") -> dict:
    with db() as conn:
        cur = conn.execute(
            "UPDATE deck SET name = ?, description = ? WHERE id = ?",
            ((name or "").strip(), (description or "").strip(), deck_id),
        )
        if cur.rowcount == 0:
            raise AppError("Baralho não encontrado")
    return {"ok": True}


def delete_deck(deck_id: int) -> dict:
    with db() as conn:
        conn.execute("DELETE FROM deck WHERE id = ?", (deck_id,))
    return {"ok": True}


# --------------------------------------------------------------------------- questões

def list_cards(deck_id: int, q: str = "", limit: int = 200, offset: int = 0) -> list[dict]:
    termo = (q or "").strip()
    like = f"%{termo}%"
    with db() as conn:
        rows = conn.execute(
            f"""
            SELECT {CARD_SELECT}
            FROM card c LEFT JOIN schedule s ON s.card_id = c.id
            WHERE c.deck_id = ? AND (? = '' OR c.question LIKE ? OR c.tags LIKE ?)
            ORDER BY c.id DESC LIMIT ? OFFSET ?
            """,
            (deck_id, termo, like, like, limit, offset),
        ).fetchall()
    return [card_row_to_dict(r) for r in rows]


def create_card(deck_id: int, kind: str = "quiz", question: str = "", options: list[str] | None = None,
                answer: str = "", explanation: str = "", tags: str = "",
                source: str = "manual") -> dict:
    question = (question or "").strip()
    if not question:
        raise AppError("Escreva a pergunta")

    card = {"kind": kind, "question": question, "explanation": (explanation or "").strip(),
            "tags": (tags or "").strip()}
    if kind == "quiz":
        opts = [o.strip() for o in (options or []) if o and o.strip()]
        if len(opts) < 2:
            raise AppError("Um quiz precisa de pelo menos 2 alternativas")
        idx = importer.resolve_answer_index(answer, opts)
        if idx is None:
            raise AppError("Marque qual alternativa é a correta")
        card["options"], card["answer"] = opts, str(idx)
    else:
        if not (answer or "").strip():
            raise AppError("O flashcard precisa de um verso")
        card["options"], card["answer"] = [], answer.strip()

    with db() as conn:
        if not conn.execute("SELECT 1 FROM deck WHERE id = ?", (deck_id,)).fetchone():
            raise AppError("Baralho não encontrado")
        card_id = insert_card(conn, deck_id, card, source)
    return {"id": card_id}


def update_card(card_id: int, question: str | None = None, options: list[str] | None = None,
                answer: str | None = None, explanation: str | None = None,
                tags: str | None = None, suspended: bool | None = None) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM card WHERE id = ?", (card_id,)).fetchone()
        if not row:
            raise AppError("Questão não encontrada")

        opts = json.loads(row["options"] or "[]")
        if options is not None:
            opts = [o.strip() for o in options if o and o.strip()]

        novo_answer = row["answer"]
        if answer is not None:
            if row["kind"] == "quiz":
                idx = importer.resolve_answer_index(answer, opts)
                if idx is None:
                    raise AppError("Marque qual alternativa é a correta")
                novo_answer = str(idx)
            else:
                novo_answer = answer.strip()

        conn.execute(
            "UPDATE card SET question = ?, options = ?, answer = ?, explanation = ?,"
            " tags = ?, suspended = ?, updated_at = ? WHERE id = ?",
            (
                (question or row["question"]).strip(),
                json.dumps(opts, ensure_ascii=False),
                novo_answer,
                row["explanation"] if explanation is None else explanation.strip(),
                row["tags"] if tags is None else tags.strip(),
                row["suspended"] if suspended is None else int(bool(suspended)),
                now_iso(),
                card_id,
            ),
        )
    return {"ok": True}


def delete_card(card_id: int) -> dict:
    with db() as conn:
        conn.execute("DELETE FROM card WHERE id = ?", (card_id,))
    return {"ok": True}


def reset_card(card_id: int) -> dict:
    """Zera o progresso da questão: ela volta a ser tratada como nova."""
    with db() as conn:
        conn.execute(
            "UPDATE schedule SET state='new', step=0, ease=2.5, interval_min=0,"
            " due_at=?, reps=0, lapses=0, last_review=NULL WHERE card_id = ?",
            (now_iso(), card_id),
        )
    return {"ok": True}


# -------------------------------------------------------------------------- importar

def preview_import(content: str = "", format: str = "auto") -> dict:
    cards, errors = importer.parse(content, format)
    formato = importer.detect_format(content) if format == "auto" else format
    return {"cards": cards, "errors": errors, "format": formato}


def import_cards(deck_id: int | None = None, deck_name: str = "", content: str = "",
                 format: str = "auto", cards: list[dict] | None = None) -> dict:
    if cards is not None:  # lote já pronto (pré-visualização da IA)
        aceitos, errors = [], []
        for i, raw in enumerate(cards, 1):
            if raw.get("question") and raw.get("answer") not in (None, ""):
                aceitos.append(raw)
            else:
                errors.append(f"item {i}: incompleto")
        origem = "ia"
    else:
        aceitos, errors = importer.parse(content, format)
        origem = "import"

    if not aceitos:
        raise AppError("Nenhuma questão reconhecida. " + (errors[0] if errors else ""))

    with db() as conn:
        destino = resolve_deck(conn, deck_id, deck_name)
        for card in aceitos:
            insert_card(conn, destino, card, origem)
    return {"deck_id": destino, "imported": len(aceitos), "errors": errors}


# ---------------------------------------------------------------------------- estudo

def start_session(deck_id: int | None = None) -> dict:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO session (deck_id, started_at) VALUES (?, ?)", (deck_id, now_iso())
        )
        return {"session_id": cur.lastrowid}


def end_session(session_id: int) -> dict:
    with db() as conn:
        conn.execute("UPDATE session SET ended_at = ? WHERE id = ?", (now_iso(), session_id))
    return {"ok": True}


def next_card(deck_id: int | None = None, mode: str = "due", exclude: int = 0) -> dict:
    deck_filter = "AND c.deck_id = :deck" if deck_id else ""
    params: dict = {"now": now_iso(), "deck": deck_id, "exclude": exclude}

    with db() as conn:
        if mode == "cram":
            row = conn.execute(
                f"""SELECT {CARD_SELECT}
                    FROM card c JOIN schedule s ON s.card_id = c.id
                    WHERE c.suspended = 0 {deck_filter} AND c.id != :exclude
                    ORDER BY RANDOM() LIMIT 1""",
                params,
            ).fetchone()
        else:
            row = conn.execute(
                f"""SELECT {CARD_SELECT}
                    FROM card c JOIN schedule s ON s.card_id = c.id
                    WHERE c.suspended = 0 AND s.due_at <= :now {deck_filter} AND c.id != :exclude
                    ORDER BY s.due_at ASC LIMIT 1""",
                params,
            ).fetchone()
            if row is None and exclude:  # só sobrou o cartão recém-respondido
                params["exclude"] = 0
                row = conn.execute(
                    f"""SELECT {CARD_SELECT}
                        FROM card c JOIN schedule s ON s.card_id = c.id
                        WHERE c.suspended = 0 AND s.due_at <= :now {deck_filter}
                        ORDER BY s.due_at ASC LIMIT 1""",
                    params,
                ).fetchone()

        if row is None:
            upcoming = conn.execute(
                f"""SELECT MIN(s.due_at) AS next_due, COUNT(*) AS restantes
                    FROM card c JOIN schedule s ON s.card_id = c.id
                    WHERE c.suspended = 0 {deck_filter}""",
                params,
            ).fetchone()
            return {"card": None, "next_due": upcoming["next_due"],
                    "remaining": upcoming["restantes"] or 0}

        due_left = conn.execute(
            f"""SELECT COUNT(*) AS n FROM card c JOIN schedule s ON s.card_id = c.id
                WHERE c.suspended = 0 AND s.due_at <= :now {deck_filter}""",
            params,
        ).fetchone()["n"]

    # Em quiz a resposta certa só vai para a tela depois que o usuário responde.
    card = card_row_to_dict(row, with_answer=row["kind"] == "flash")
    return {"card": card, "due_left": due_left, "grade_preview": preview(SchedState.from_row(row))}


def check_answer(card_id: int, choice: int | None = None) -> dict:
    """Corrige a resposta e devolve o feedback — ainda não agenda nada."""
    with db() as conn:
        row = conn.execute(
            f"SELECT {CARD_SELECT} FROM card c JOIN schedule s ON s.card_id = c.id WHERE c.id = ?",
            (card_id,),
        ).fetchone()
    if not row:
        raise AppError("Questão não encontrada")

    options = json.loads(row["options"] or "[]")
    correct_index = int(row["answer"]) if row["kind"] == "quiz" and row["answer"].isdigit() else -1
    correct = choice is not None and choice == correct_index

    return {
        "correct": correct,
        "correct_index": correct_index,
        "correct_text": options[correct_index] if 0 <= correct_index < len(options) else row["answer"],
        "chosen_text": options[choice] if choice is not None and 0 <= choice < len(options) else "",
        "explanation": row["explanation"],
        "grade_preview": preview(SchedState.from_row(row)),
        "stats": {"reps": row["reps"], "lapses": row["lapses"]},
    }


def grade_card(card_id: int, grade: int, session_id: int | None = None,
               choice: int | None = None, ms: int = 0) -> dict:
    grade = max(0, min(int(grade), 3))
    with db() as conn:
        row = conn.execute(
            "SELECT c.kind, c.answer, s.* FROM card c JOIN schedule s ON s.card_id = c.id "
            "WHERE c.id = ?",
            (card_id,),
        ).fetchone()
        if not row:
            raise AppError("Questão não encontrada")

        correct = grade > 0
        if row["kind"] == "quiz":
            correct_index = int(row["answer"]) if row["answer"].isdigit() else -1
            correct = choice is not None and choice == correct_index
            if not correct:
                grade = 0  # errou é errou: não aceitamos nota melhor

        new = apply_grade(SchedState.from_row(row), grade)
        due_at = due_from(new.interval_min)
        ts = now_iso()

        conn.execute(
            "UPDATE schedule SET state=?, step=?, ease=?, interval_min=?, due_at=?,"
            " reps=?, lapses=?, last_review=? WHERE card_id=?",
            (new.state, new.step, new.ease, new.interval_min, due_at,
             new.reps, new.lapses, ts, card_id),
        )
        conn.execute(
            "INSERT INTO review (card_id, session_id, reviewed_at, grade, correct,"
            " answer_given, ms, interval_after) VALUES (?,?,?,?,?,?,?,?)",
            (card_id, session_id, ts, grade, int(correct),
             "" if choice is None else str(choice), max(0, int(ms)), new.interval_min),
        )
        if session_id:
            conn.execute(
                "UPDATE session SET reviewed = reviewed + 1, correct = correct + ?,"
                " ms_total = ms_total + ? WHERE id = ?",
                (int(correct), max(0, int(ms)), session_id),
            )

    return {
        "correct": correct,
        "grade": grade,
        "state": new.state,
        "due_at": due_at,
        "interval": humanize(new.interval_min),
        "message": f"Volta em {humanize(new.interval_min)}",
    }


# --------------------------------------------------------------------- estatísticas

def stats(deck_id: int | None = None) -> dict:
    deck_join = "JOIN card c ON c.id = r.card_id"
    deck_where = "WHERE c.deck_id = :deck" if deck_id else ""
    agora = datetime.now(timezone.utc)
    params = {
        "deck": deck_id,
        "now": now_iso(),
        "since": (agora - timedelta(days=13)).isoformat(timespec="seconds"),
        "until": (agora + timedelta(days=7)).isoformat(timespec="seconds"),
        "since_mapa": (agora - timedelta(days=90)).isoformat(timespec="seconds"),
    }

    with db() as conn:
        totals = conn.execute(
            f"""SELECT COUNT(*) AS total,
                   COALESCE(SUM(CASE WHEN s.state = 'new' THEN 1 ELSE 0 END), 0) AS novos,
                   COALESCE(SUM(CASE WHEN s.state IN ('learning','relearning') THEN 1 ELSE 0 END), 0) AS aprendendo,
                   COALESCE(SUM(CASE WHEN s.state = 'review' THEN 1 ELSE 0 END), 0) AS maduros,
                   COALESCE(SUM(CASE WHEN c.suspended = 0 AND s.due_at <= :now THEN 1 ELSE 0 END), 0) AS devidos
                FROM card c JOIN schedule s ON s.card_id = c.id
                {'WHERE c.deck_id = :deck' if deck_id else ''}""",
            params,
        ).fetchone()

        overall = conn.execute(
            f"""SELECT COUNT(*) AS revisoes, COALESCE(SUM(r.correct), 0) AS acertos
                FROM review r {deck_join} {deck_where}""",
            params,
        ).fetchone()

        daily = conn.execute(
            f"""SELECT date(r.reviewed_at, 'localtime') AS dia, COUNT(*) AS revisoes,
                       COALESCE(SUM(r.correct), 0) AS acertos
                FROM review r {deck_join}
                {deck_where + ' AND' if deck_where else 'WHERE'} r.reviewed_at >= :since
                GROUP BY dia ORDER BY dia""",
            params,
        ).fetchall()

        forecast = conn.execute(
            f"""SELECT date(s.due_at, 'localtime') AS dia, COUNT(*) AS n
                FROM card c JOIN schedule s ON s.card_id = c.id
                WHERE c.suspended = 0 AND s.due_at <= :until
                  {'AND c.deck_id = :deck' if deck_id else ''}
                GROUP BY dia ORDER BY dia""",
            params,
        ).fetchall()

        hardest = conn.execute(
            f"""SELECT c.id, c.question, s.lapses, s.reps,
                       COALESCE(SUM(r.correct), 0) AS acertos, COUNT(r.id) AS tentativas
                FROM card c JOIN schedule s ON s.card_id = c.id
                LEFT JOIN review r ON r.card_id = c.id
                WHERE s.lapses > 0 {'AND c.deck_id = :deck' if deck_id else ''}
                GROUP BY c.id ORDER BY s.lapses DESC, tentativas DESC LIMIT 8""",
            params,
        ).fetchall()

        mapa = conn.execute(
            f"""SELECT date(r.reviewed_at, 'localtime') AS dia, COUNT(*) AS n
                FROM review r {deck_join}
                {deck_where + ' AND' if deck_where else 'WHERE'} r.reviewed_at >= :since_mapa
                GROUP BY dia ORDER BY dia""",
            params,
        ).fetchall()

        streak_rows = conn.execute(
            "SELECT DISTINCT date(reviewed_at, 'localtime') AS dia FROM review ORDER BY dia DESC"
        ).fetchall()

    # Sequência de dias seguidos estudando (ontem ainda conta, hoje pode estar começando).
    dias = [r["dia"] for r in streak_rows]
    streak = 0
    hoje = date.today()
    if dias and dias[0] in (hoje.isoformat(), (hoje - timedelta(days=1)).isoformat()):
        cursor = date.fromisoformat(dias[0])
        for dia in dias:
            if dia == cursor.isoformat():
                streak += 1
                cursor -= timedelta(days=1)
            else:
                break

    revisoes = overall["revisoes"] or 0
    return {
        "totals": dict(totals),
        "reviews": revisoes,
        "accuracy": round(100 * (overall["acertos"] or 0) / revisoes) if revisoes else 0,
        "streak": streak,
        "daily": [dict(r) for r in daily],
        "heatmap": {r["dia"]: r["n"] for r in mapa},
        "forecast": [dict(r) for r in forecast],
        "hardest": [dict(r) for r in hardest],
    }


# -------------------------------------------------------------------------------- IA

CHAVE_API = "gemini_api_key"


def _chave_guardada(conn) -> tuple[str, bool]:
    """Devolve (chave_em_claro, ilegivel).

    - Valor cifrado: decifra. Se o Windows não deixar (banco copiado de outro usuário ou
      computador), `ilegivel` fica True e a chave conta como "não configurada".
    - Valor antigo em texto puro (versões anteriores): usa e já regrava cifrado.
    """
    bruto = get_setting(conn, CHAVE_API)
    if not bruto:
        return "", False
    claro = cofre.revelar(bruto)
    if claro is None:
        return "", True
    if not cofre.ja_protegido(bruto):
        try:
            set_setting(conn, CHAVE_API, cofre.proteger(claro))
            conn.commit()
            _precisa_depurar.append(True)  # limpa o rastro do texto puro quando fechar a conexão
        except OSError:
            pass  # sem cofre disponível: segue funcionando, sem migrar
    return claro, False


_precisa_depurar: list[bool] = []


def _depurar_se_preciso() -> None:
    if _precisa_depurar:
        _precisa_depurar.clear()
        depurar_arquivo()


def ai_status() -> dict:
    """Nunca devolve a chave — só se ela existe e de onde vem."""
    with db() as conn:
        guardada, ilegivel = _chave_guardada(conn)
        model = get_setting(conn, "ai_model", ai.DEFAULT_MODEL)
    _depurar_se_preciso()
    return {
        "provider": "Gemini",
        "configured": bool(ai.resolve_api_key(guardada)),
        "from_env": ai.key_from_env(),
        "key_unreadable": ilegivel,
        "model": model,
    }


def ai_models() -> dict:
    with db() as conn:
        guardada, _ = _chave_guardada(conn)
    _depurar_se_preciso()
    return {"models": ai.list_models(guardada)}


def save_key(api_key: str = "") -> dict:
    chave = (api_key or "").strip()
    with db() as conn:
        try:
            set_setting(conn, CHAVE_API, cofre.proteger(chave))  # "" apaga a chave
        except OSError as exc:
            raise AppError(f"Não consegui proteger a chave com o Windows: {exc}")
    depurar_arquivo()  # a chave anterior (ou o texto puro de versões antigas) não pode sobrar no arquivo
    return {"ok": True}


def save_model(model: str = "") -> dict:
    with db() as conn:
        set_setting(conn, "ai_model", (model or "").strip() or ai.DEFAULT_MODEL)
    return {"ok": True}


def ai_generate(material: str = "", n: int = 10, kind: str = "quiz",
                difficulty: str = "médio", notes: str = "") -> dict:
    with db() as conn:
        guardada, _ = _chave_guardada(conn)
        model = get_setting(conn, "ai_model", ai.DEFAULT_MODEL)
    _depurar_se_preciso()
    try:
        # um único pedido; devolve {"cards": [...], "aviso": texto ou None}
        return ai.generate(material=material, n=n, kind=kind, difficulty=difficulty,
                           notes=notes, api_key=guardada, model=model)
    except RuntimeError as exc:
        raise AppError(str(exc))


# ------------------------------------------------------------------------- ajustes

TEMAS = ("auto", "claro", "escuro")


def get_settings() -> dict:
    from .paths import data_dir

    with db() as conn:
        tema = get_setting(conn, "theme", "auto")
    return {"theme": tema if tema in TEMAS else "auto", "version": __version__,
            "data_dir": str(data_dir())}


def save_settings(theme: str = "auto") -> dict:
    if theme not in TEMAS:
        raise AppError("Tema desconhecido")
    with db() as conn:
        set_setting(conn, "theme", theme)
    return {"ok": True}


def exportar_backup(destino: str) -> dict:
    """Copia o banco para `destino` SEM a chave da API.

    Backup costuma ir parar em pendrive, e-mail ou pasta na nuvem; a chave não tem por
    que viajar junto (e cifrada com DPAPI ela nem abriria em outro computador).
    """
    from pathlib import Path

    from .db import connect

    alvo = Path(destino)
    if alvo.suffix.lower() != ".db":
        alvo = alvo.with_name(alvo.name + ".db")
    if not alvo.parent.exists():
        raise AppError("A pasta escolhida não existe.")

    origem = connect()
    copia = sqlite3.connect(alvo)
    try:
        origem.backup(copia)
        copia.execute("PRAGMA secure_delete = ON")
        copia.execute("DELETE FROM setting WHERE key = ?", (CHAVE_API,))
        copia.commit()
        copia.isolation_level = None
        copia.execute("VACUUM")  # sem isto o texto apagado ainda sobraria dentro do arquivo
    finally:
        copia.close()
        origem.close()
    return {"ok": True, "caminho": str(alvo), "tamanho_kb": max(1, alvo.stat().st_size // 1024)}
