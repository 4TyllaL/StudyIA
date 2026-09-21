"""Ponte entre a janela e o Python.

A interface continua falando por "caminhos" (`/api/decks`, `/api/study/next`), só que
em vez de sair pela rede a chamada cai direto numa função de `service.py`. Não há
servidor, porta nem localhost envolvidos.
"""
from __future__ import annotations

import inspect
import os
import re
import traceback
import webbrowser
from datetime import date
from urllib.parse import parse_qs, urlsplit

from . import cofre, service
from .db import init_db
from .service import AppError

LINKS_PERMITIDOS = (
    "https://aistudio.google.com/",  # onde o usuário cria a chave do Gemini
    "https://github.com/4TyllaL/",   # link do autor na aba Ajustes
)

# (método, expressão do caminho, função). Os grupos nomeados viram argumentos.
ROTAS: list[tuple[str, re.Pattern[str], object]] = [
    ("GET", r"/api/decks", service.list_decks),
    ("POST", r"/api/decks", service.create_deck),
    ("PATCH", r"/api/decks/(?P<deck_id>\d+)", service.update_deck),
    ("DELETE", r"/api/decks/(?P<deck_id>\d+)", service.delete_deck),
    ("GET", r"/api/decks/(?P<deck_id>\d+)/cards", service.list_cards),

    ("POST", r"/api/cards", service.create_card),
    ("PATCH", r"/api/cards/(?P<card_id>\d+)", service.update_card),
    ("DELETE", r"/api/cards/(?P<card_id>\d+)", service.delete_card),
    ("POST", r"/api/cards/(?P<card_id>\d+)/reset", service.reset_card),

    ("POST", r"/api/import/preview", service.preview_import),
    ("POST", r"/api/import", service.import_cards),

    ("POST", r"/api/study/start", service.start_session),
    ("POST", r"/api/study/end", service.end_session),
    ("GET", r"/api/study/next", service.next_card),
    ("POST", r"/api/study/answer", service.check_answer),
    ("POST", r"/api/study/grade", service.grade_card),

    ("GET", r"/api/stats", service.stats),

    ("GET", r"/api/settings", service.get_settings),
    ("POST", r"/api/settings", service.save_settings),

    ("GET", r"/api/ai/status", service.ai_status),
    ("GET", r"/api/ai/models", service.ai_models),
    ("POST", r"/api/ai/key", service.save_key),
    ("POST", r"/api/ai/model", service.save_model),
    ("POST", r"/api/ai/generate", service.ai_generate),
]
ROTAS = [(m, re.compile(f"^{p}$"), f) for m, p, f in ROTAS]  # type: ignore[misc]


def _converter(func, bruto: dict) -> dict:
    """Ajusta os valores ao que a função espera e descarta o que ela não aceita.

    Caminho e query chegam sempre como texto ('3', ''), então usamos as anotações
    para devolver int/bool antes de chamar.
    """
    assinatura = inspect.signature(func)
    pronto: dict = {}
    for nome, param in assinatura.parameters.items():
        if nome not in bruto:
            continue
        valor = bruto[nome]
        anotacao = str(param.annotation)
        if isinstance(valor, str):
            if valor == "" and "None" in anotacao:
                valor = None
            elif "int" in anotacao and re.fullmatch(r"-?\d+", valor):
                valor = int(valor)
            elif "bool" in anotacao:
                valor = valor.lower() in ("1", "true", "sim")
        pronto[nome] = valor
    return pronto


def rotear(path: str, method: str = "GET", body: dict | None = None):
    partes = urlsplit(path)
    query = {k: v[0] for k, v in parse_qs(partes.query).items()}

    for metodo, padrao, func in ROTAS:
        if metodo != method.upper():
            continue
        m = padrao.match(partes.path)
        if not m:
            continue
        bruto = {**m.groupdict(), **query, **(body or {})}
        return func(**_converter(func, bruto))

    raise AppError(f"Chamada desconhecida: {method} {partes.path}")


class Api:
    """Objeto exposto à interface como `window.pywebview.api`."""

    def __init__(self) -> None:
        init_db()

    def request(self, pedido: dict | None = None):
        pedido = pedido or {}
        try:
            dados = rotear(
                pedido.get("path", ""),
                pedido.get("method", "GET"),
                pedido.get("body") or None,
            )
            return {"data": dados}
        except AppError as exc:
            return {"error": cofre.ocultar_chaves(str(exc))}
        except Exception as exc:  # erro nosso: mostra algo útil em vez de travar a tela
            traceback.print_exc()
            return {"error": cofre.ocultar_chaves(f"Erro interno ({type(exc).__name__}): {exc}")}

    # ---- ações que precisam do sistema (janelas de arquivo, Explorador) ----

    def exportar_backup(self) -> dict:
        """Abre 'Salvar como…' e grava uma cópia dos dados, sem a chave da API."""
        import webview

        try:
            nome = f"StudyIA-backup-{date.today().isoformat()}.db"
            escolhido = webview.windows[0].create_file_dialog(
                webview.FileDialog.SAVE, save_filename=nome,
                file_types=("Banco do StudyIA (*.db)",),
            )
            if not escolhido:
                return {"cancelado": True}
            caminho = escolhido if isinstance(escolhido, str) else escolhido[0]
            return {"data": service.exportar_backup(caminho)}
        except AppError as exc:
            return {"error": str(exc)}
        except Exception as exc:
            return {"error": cofre.ocultar_chaves(f"Não consegui salvar o backup: {exc}")}

    def abrir_pasta_dados(self) -> dict:
        from .paths import data_dir

        pasta = data_dir()
        pasta.mkdir(parents=True, exist_ok=True)
        os.startfile(str(pasta))  # Explorador de Arquivos
        return {"ok": True}

    def abrir_link(self, url: str = "") -> dict:
        """Links externos abrem no navegador do sistema, não dentro da janela.

        Só endereços conhecidos: a interface é a única que chama isto, mas se algum dia
        um texto malicioso (ex.: vindo de uma questão importada) conseguisse rodar
        script, ele não poderia abrir sites arbitrários por aqui.
        """
        if url.startswith(LINKS_PERMITIDOS):
            webbrowser.open(url)
        return {"ok": True}
