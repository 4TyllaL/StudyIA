"""Teste de ponta a ponta do modo debate, pela janela real, contra um Gemini falso.

Envia um PDF de verdade pela interface, encontra os temas, debate (com erro de servidor no
meio), encerra e adiciona as questões sugeridas a um baralho. Confere também que cada fala
do debate é UM pedido e que o material inteiro não é reenviado a cada rodada.

    .venv\\Scripts\\python.exe tools\\smoke_debate.py
    (com STUDYIA_CAPTURAS=<pasta>, salva uma imagem de cada etapa)
"""
from __future__ import annotations

import base64
import ctypes
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

BANCO_TESTE = Path(tempfile.gettempdir()) / "studyia-smoke-debate"
shutil.rmtree(BANCO_TESTE, ignore_errors=True)
os.environ["STUDYIA_DATA"] = str(BANCO_TESTE)
os.environ["STUDYIA_TIMEOUT_S"] = "5"

import arquivos_exemplo  # noqa: E402
import gemini_falso as F  # noqa: E402
import webview  # noqa: E402

servidor, porta = F.iniciar()
os.environ["GOOGLE_GEMINI_BASE_URL"] = f"http://127.0.0.1:{porta}"

from app.bridge import Api  # noqa: E402
from app.paths import WEB_DIR  # noqa: E402

TITULO = "StudyIA (teste debate)"
CAPTURAS = os.environ.get("STUDYIA_CAPTURAS")
falhas: list[str] = []
janela = None
concluiu = False
info: dict = {}


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


def capturar(nome: str) -> None:
    """Imagem da janela (só com STUDYIA_CAPTURAS), para conferir o visual."""
    if not CAPTURAS:
        return
    from ctypes import wintypes

    from PIL import Image

    time.sleep(0.5)
    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    hwnd = u.FindWindowW(None, TITULO)
    r = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    larg, alt = r.right - r.left, r.bottom - r.top
    hdc = u.GetWindowDC(hwnd)
    mem = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, larg, alt)
    g.SelectObject(mem, bmp)
    u.PrintWindow(hwnd, mem, 2)

    class BIH(ctypes.Structure):
        _fields_ = [("s", wintypes.DWORD), ("w", wintypes.LONG), ("h", wintypes.LONG), ("p", wintypes.WORD),
                    ("b", wintypes.WORD), ("c", wintypes.DWORD), ("si", wintypes.DWORD), ("x", wintypes.LONG),
                    ("y", wintypes.LONG), ("u", wintypes.DWORD), ("i", wintypes.DWORD)]

    bih = BIH(ctypes.sizeof(BIH), larg, -alt, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(larg * alt * 4)
    g.GetDIBits(mem, bmp, 0, alt, buf, ctypes.byref(bih), 0)
    Path(CAPTURAS).mkdir(parents=True, exist_ok=True)
    Image.frombuffer("RGBA", (larg, alt), buf, "raw", "BGRA", 0, 1).convert("RGB").save(Path(CAPTURAS) / f"{nome}.png")
    g.DeleteObject(bmp); g.DeleteDC(mem); u.ReleaseDC(hwnd, hdc)


def enviar_arquivo(nome: str, dados: bytes) -> None:
    """Passa um arquivo pelo mesmo caminho do "Escolher arquivo…" (File -> lerMaterial)."""
    b64 = base64.b64encode(dados).decode()
    js(f"(() => {{ const b = atob('{b64}'); const u = new Uint8Array(b.length);"
       f" for (let i = 0; i < b.length; i++) u[i] = b.charCodeAt(i);"
       f" lerMaterial(new File([u], {json.dumps(nome)})); }})()")


def mensagens_no_banco() -> int:
    with sqlite3.connect(BANCO_TESTE / "studyia.db") as c:
        row = c.execute("select mensagens from debate order by id desc limit 1").fetchone()
    return len(json.loads(row[0])) if row else 0


def roteiro(_janela=None) -> None:
    global concluiu
    try:
        esperar("!!window.pywebview && !!document.querySelector('#deck-list')", "interface carregou")
        js("window.__erros = [];"
           "addEventListener('error', e => __erros.push('erro: ' + e.message));"
           "addEventListener('unhandledrejection', e => __erros.push('promessa: ' + (e.reason && e.reason.message)));")
        js("api('/api/ai/key', { method: 'POST', body: { api_key: 'AIza' + 'Sy-fake-test-key-0123456789abcdef' } })")
        time.sleep(0.6)

        secao("abrir a aba e ler um PDF")
        js("switchView('debate')")
        esperar("!document.querySelector('#debate-etapa-material').hidden", "etapa do material aparece")
        checar(not js("document.querySelector('#debate-aviso-chave').textContent.trim()"), "sem aviso de chave (ela está salva)")
        esperar("document.querySelector('#btn-debate-temas').disabled", "botão desativado sem material")
        F.Estado.pedidos = 0
        enviar_arquivo("lei-8112.pdf", arquivos_exemplo.pdf())
        esperar("document.querySelector('#debate-material').value.includes('30 dias')", "texto do PDF no campo")
        checar("lei-8112.pdf" in js("document.querySelector('#debate-material-nome').textContent"), "nome do arquivo aparece")
        checar(F.Estado.pedidos == 0, "ler o arquivo não fala com o Gemini")
        capturar("1-material")

        secao("encontrar os temas")
        js("document.querySelector('#btn-debate-temas').click()")
        esperar("document.querySelectorAll('.tema').length === 3", "3 temas aparecem")
        checar(F.Estado.pedidos == 1, f"1 pedido (feitos: {F.Estado.pedidos})")
        tamanho_temas = F.Estado.tamanhos[-1]
        capturar("2-temas")

        secao("começar o debate")
        js("document.querySelector('[data-debater=\"0\"]').click()")
        esperar("document.querySelectorAll('#debate-chat .msg.ia').length === 1", "fala de abertura da IA")
        checar(F.Estado.pedidos == 2, f"1 pedido para abrir (total: {F.Estado.pedidos})")
        checar(bool(js("document.querySelector('.debate-cabeca .tese')")), "tese do tema no topo")
        checar(bool(js("document.querySelector('#btn-voz') || document.querySelector('.voz-aviso')")), "controle de voz presente")
        info["vozes"] = js("vozesLocais().map(v => v.name + ' (' + v.lang + ')')")

        secao("responder")
        js("document.querySelector('#debate-resposta').value = 'Acho que o prazo de recurso é de 10 dias.'")
        js("document.querySelector('#btn-debate-enviar').click()")
        esperar("document.querySelectorAll('#debate-chat .msg:not(.pendente)').length === 3", "resposta e réplica no chat")
        checar(F.Estado.pedidos == 3, f"1 pedido por resposta (total: {F.Estado.pedidos})")
        checar(bool(js("document.querySelector('#debate-chat .lacuna')")), "falha da resposta aparece marcada")
        corpo_turno = F.Estado.corpos[-1].decode("utf-8", "replace")
        checar("abandono de cargo" not in corpo_turno and "demissao" not in corpo_turno,
               "o material inteiro NÃO é reenviado na rodada")
        checar(F.Estado.tamanhos[-1] < tamanho_temas * 3, f"pedido da rodada pequeno ({F.Estado.tamanhos[-1]} bytes)")
        checar(mensagens_no_banco() == 3, "conversa gravada no banco")

        js("const c = document.querySelector('#debate-resposta'); c.value = 'Mas a defesa é em 10 dias.';"
           "c.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true, bubbles: true }))")
        esperar("document.querySelectorAll('#debate-chat .msg:not(.pendente)').length === 5", "Ctrl+Enter envia")
        capturar("3-conversa")

        secao("servidor fora do ar no meio do debate")
        F.Estado.modo = "503"
        js("document.querySelector('#debate-resposta').value = 'Resposta que vai falhar'")
        js("document.querySelector('#btn-debate-enviar').click()")
        esperar("document.querySelector('#debate-erro-palco .notice.error') !== null", "erro aparece na tela", 15)
        checar(js("document.querySelector('#debate-resposta').value") == "Resposta que vai falhar", "texto volta para o campo")
        checar(js("document.querySelectorAll('#debate-chat .msg').length") == 5, "bolhas provisórias somem")
        checar(mensagens_no_banco() == 5, "nada gravado quando falha")
        checar(js("document.querySelector('#btn-debate-enviar').disabled") is False, "botão volta a funcionar")
        F.Estado.modo = "ok"

        secao("encerrar e aproveitar as questões")
        js("document.querySelector('#btn-debate-encerrar').click()")
        esperar("document.querySelectorAll('.avaliacao li').length >= 2", "avaliação aparece")
        esperar("document.querySelectorAll('#debate-cards .card-item').length === 3", "3 questões sugeridas")
        checar(not js("document.querySelector('#debate-resposta')"), "campo de resposta some depois de encerrar")
        capturar("4-resumo")
        js("document.querySelector('#debate-deck-new').value = 'Debate - Lei 8.112';"
           "document.querySelector('#debate-deck-new').dispatchEvent(new Event('input'));"
           "document.querySelector('#btn-debate-salvar').click()")
        esperar("document.querySelector('#toast').textContent.includes('adicionadas')", "questões adicionadas")
        time.sleep(0.5)
        with sqlite3.connect(BANCO_TESTE / "studyia.db") as c:
            n = c.execute("select count(*) from card c join deck d on d.id = c.deck_id where d.name = 'Debate - Lei 8.112'").fetchone()[0]
        checar(n == 3, f"3 questões no baralho novo (gravadas: {n})")

        secao("voz")
        if info["vozes"]:
            # volume zero: o teste confere que a fala acontece, sem falar alto no computador
            js("const _U = window.SpeechSynthesisUtterance;"
               "window.SpeechSynthesisUtterance = function (t) { const u = new _U(t); u.volume = 0; return u; }")
            js("document.querySelector('#btn-voz').click()")
            time.sleep(0.4)
            with sqlite3.connect(BANCO_TESTE / "studyia.db") as c:
                salvo = json.loads(c.execute("select value from setting where key='voz'").fetchone()[0])
            checar(salvo["ativa"] is True, "ligar a voz fica salvo")
            checar(bool(js("speechSynthesis.speaking || speechSynthesis.pending")), "a IA fala ao ligar a voz")
            js("pararFala()")
        else:
            checar(bool(js("document.querySelector('.voz-aviso')")), "sem voz instalada: aviso no lugar dos controles")

        secao("debates recentes")
        js("document.querySelector('#btn-debate-voltar').click()")
        esperar("document.querySelectorAll('[data-abrir-debate]').length === 1", "debate aparece na lista")
        checar(not js("document.querySelector('#debate-palco').innerHTML.trim()"), "tela anterior foi descartada")
        checar("encerrado" in js("document.querySelector('#debate-recentes').textContent"), "marcado como encerrado")
        js("document.querySelector('[data-abrir-debate]').click()")
        esperar("document.querySelectorAll('.avaliacao li').length >= 2", "reabrir mostra o resumo")

        secao("Word e arquivo inválido")
        esperar("!!document.querySelector('#btn-debate-voltar')", "botão de voltar")
        js("document.querySelector('#btn-debate-voltar').click()")
        enviar_arquivo("resumo.docx", arquivos_exemplo.docx())
        esperar("document.querySelector('#debate-material').value.includes('abandono de cargo')", "texto do .docx no campo")
        enviar_arquivo("antigo.doc", b"x" * 100)
        esperar("document.querySelector('#debate-erro').textContent.includes('.docx')", "arquivo .doc explica o que fazer")

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
    janela = webview.create_window(TITULO, (WEB_DIR / "index.html").as_uri(),
                                   js_api=Api(), width=1180, height=860)
    webview.start(roteiro, janela)

    print(f"\nvozes locais em português neste Windows: {info.get('vozes')}")
    print("=" * 52)
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
