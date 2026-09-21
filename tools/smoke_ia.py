"""Teste de ponta a ponta da geração por IA, pela janela real, contra um Gemini falso.

Prova o caminho que o usuário usa — colar material, gerar, escolher, adicionar ao baralho —
e os defeitos que já apareceram: pedido repetido em erro de servidor, espera infinita e erro
que some da tela. Não gasta cota nem precisa de chave de verdade.

    .venv\\Scripts\\python.exe tools\\smoke_ia.py
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(Path(__file__).resolve().parent))

BANCO_TESTE = Path(tempfile.gettempdir()) / "studyia-smoke-ia"
shutil.rmtree(BANCO_TESTE, ignore_errors=True)
os.environ["STUDYIA_DATA"] = str(BANCO_TESTE)
os.environ["STUDYIA_TIMEOUT_S"] = "3"        # antes de importar app.ai: o "travado" tem que estourar rápido

import gemini_falso as F  # noqa: E402
import webview  # noqa: E402

servidor, porta = F.iniciar()
os.environ["GOOGLE_GEMINI_BASE_URL"] = f"http://127.0.0.1:{porta}"

from app.bridge import Api  # noqa: E402
from app.paths import WEB_DIR  # noqa: E402

falhas: list[str] = []
janela = None
concluiu = False


def js(codigo: str):
    return janela.evaluate_js(codigo)


def esperar(expressao: str, descricao: str, segundos: float = 12.0):
    limite = time.time() + segundos
    while time.time() < limite:
        try:
            valor = js(expressao)
            if valor:
                return valor
        except Exception:
            pass
        time.sleep(0.15)
    falhas.append(f"{descricao} (esperando: {expressao})")
    print(f"  FALHA  {descricao}", flush=True)
    return None


def checar(condicao, descricao: str) -> None:
    print(("  ok   " if condicao else "  FALHA") + f"  {descricao}", flush=True)
    if not condicao:
        falhas.append(descricao)


def secao(nome: str) -> None:
    print(f"\n== {nome} ==", flush=True)


def gerar(modo: str, material: str = "Lei 8.112/90, regime disciplinar. " * 30) -> None:
    """Configura o servidor falso, preenche a tela e clica em Gerar."""
    F.Estado.modo, F.Estado.pedidos, F.Estado.tamanhos = modo, 0, []
    js("document.querySelector('#ai-error').innerHTML = ''")
    js(f"document.querySelector('#ai-material').value = {json.dumps(material)}; "
       "document.querySelector('#ai-material').dispatchEvent(new Event('input'))")
    js("document.querySelector('#btn-generate').click()")


def roteiro(_janela=None) -> None:
    global concluiu
    try:
        esperar("!!window.pywebview && !!document.querySelector('#deck-list')", "interface carregou")
        js("window.__erros = [];"
           "addEventListener('error', e => __erros.push('erro: ' + e.message));"
           "addEventListener('unhandledrejection', e => __erros.push('promessa: ' + (e.reason && e.reason.message)));")

        # a chave entra pelo caminho normal do programa (e fica criptografada)
        js("api('/api/ai/key', { method: 'POST', body: { api_key: 'AIza' + 'Sy-fake-test-key-0123456789abcdef' } })")
        time.sleep(0.6)
        js("switchView('ai')")
        esperar("!!document.querySelector('.lock-badge')", "chave aparece como protegida")

        secao("geração com sucesso")
        gerar("ok")
        esperar("document.querySelectorAll('#ai-preview .card-item').length === 5", "5 questões apareceram na pré-visualização")
        checar(F.Estado.pedidos == 1, f"exatamente 1 pedido ao Gemini (feitos: {F.Estado.pedidos})")
        checar(js("document.querySelector('#btn-generate').disabled") is False, "botão liberado depois de gerar")
        checar(not js("document.querySelector('#ai-error').textContent.trim()"), "nenhum erro na tela")
        corpo = json.loads(F.Estado.corpos[0])
        cfg = corpo.get("generation_config", {})
        checar(cfg.get("max_output_tokens", 99999) <= 16000, f"teto de saída proporcional ao pedido ({cfg.get('max_output_tokens')} tokens)")

        secao("escolher quais adicionar")
        js("document.querySelector('[data-pick=\"0\"]').click()")
        esperar("document.querySelectorAll('[data-pick]:checked').length === 4", "desmarcar uma deixa 4 selecionadas")
        js("document.querySelector('#ai-deck-new').value = 'Gerado pela IA'")
        js("document.querySelector('#btn-save-ai').click()")
        esperar("document.querySelector('#toast').textContent.includes('adicionadas')", "toast de adição")
        time.sleep(0.5)
        with sqlite3.connect(BANCO_TESTE / "studyia.db") as c:
            n = c.execute("select count(*) from card where source='ia'").fetchone()[0]
        checar(n == 4, f"4 questões gravadas no banco (gravadas: {n})")

        secao("servidor sobrecarregado (503): um pedido só, erro visível")
        gerar("503")
        esperar("document.querySelector('#ai-error .notice.error') !== null", "erro aparece na página", 15)
        checar(F.Estado.pedidos == 1, f"UM pedido apesar do erro (antes eram 4; feitos: {F.Estado.pedidos})")
        checar(bool(js("document.querySelector('#ai-error').textContent.includes('sobrecarregado')")), "mensagem explica o motivo")
        checar(bool(js("document.querySelector('#ai-error').textContent.includes('não repete sozinho')")), "avisa que não repete sozinho")

        secao("limite de cota (429)")
        gerar("429")
        esperar("document.querySelector('#ai-error .notice.error') !== null", "erro de cota aparece", 15)
        checar(F.Estado.pedidos == 1, f"um pedido só (feitos: {F.Estado.pedidos})")

        secao("servidor que não responde: não fica esperando para sempre")
        inicio = time.time()
        gerar("travado")
        esperar("document.querySelector('#ai-error .notice.error') !== null", "erro de tempo esgotado aparece", 15)
        checar(time.time() - inicio < 12, f"desistiu em {time.time() - inicio:.1f}s (o teste usa limite de 3s)")
        checar(bool(js("document.querySelector('#ai-error').textContent.includes('demorou')")), "mensagem fala de demora")
        checar(F.Estado.pedidos == 1, "um pedido só, sem repetir")
        checar(js("document.querySelector('#btn-generate').disabled") is False, "botão volta a funcionar")

        secao("resposta cortada: aproveita o que veio")
        gerar("cortado")
        esperar("document.querySelectorAll('#ai-preview .card-item').length >= 1", "questões completas foram aproveitadas")
        checar(bool(js("document.querySelector('#ai-error .notice.warn') !== null")), "aviso explica que veio cortada")

        secao("resposta incompleta sem nada aproveitável")
        js("state.aiCards = []; renderAiPreview()")
        gerar("incompleto")
        esperar("document.querySelector('#ai-error .notice.error') !== null", "erro aparece")
        checar(bool(js("document.querySelector('#ai-error').textContent.includes('cortada')")), "diz que foi cortada (não 'bloqueada')")

        secao("material grande demais")
        F.Estado.pedidos = 0
        js("document.querySelector('#ai-material').value = 'x'.repeat(40001); "
           "document.querySelector('#ai-material').dispatchEvent(new Event('input'))")
        checar(js("document.querySelector('#btn-generate').disabled"), "botão trava acima de 40.000 caracteres")
        checar(F.Estado.pedidos == 0, "nenhum pedido feito")

        secao("saúde do JavaScript")
        erros = js("window.__erros")
        checar(not erros, f"nenhum erro de JS {erros or ''}")
        concluiu = True
    except Exception as exc:
        falhas.append(f"erro inesperado: {exc}")
    finally:
        janela.destroy()


def main() -> int:
    global janela
    janela = webview.create_window("StudyIA (teste IA)", (WEB_DIR / "index.html").as_uri(),
                                   js_api=Api(), width=1180, height=860)
    webview.start(roteiro, janela)

    print("\n" + "=" * 52)
    if not concluiu and not falhas:
        falhas.append("o roteiro nao chegou ao fim")
    if falhas:
        print(f"{len(falhas)} FALHA(S):")
        for f in falhas:
            print("  -", f)
        return 1
    print("tudo passou")
    return 0


if __name__ == "__main__":
    codigo = main()
    shutil.rmtree(BANCO_TESTE, ignore_errors=True)
    raise SystemExit(codigo)
