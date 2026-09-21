"""Teste de ponta a ponta da janela: abre o StudyIA e usa a interface de verdade.

Clica e digita como um usuário e confere o resultado no próprio DOM — é o jeito de validar
a ponte interface <-> Python sem servidor no meio. Usa um banco temporário, então nunca
encosta nos estudos reais. Falha se qualquer passo falhar, se o roteiro não chegar ao fim,
ou se o navegador reportar erro de JavaScript / violação da política de segurança (CSP).

    .venv\\Scripts\\python.exe tools\\smoke_desktop.py
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

BANCO_TESTE = Path(tempfile.gettempdir()) / "studyia-smoke"
shutil.rmtree(BANCO_TESTE, ignore_errors=True)
os.environ["STUDYIA_DATA"] = str(BANCO_TESTE)

import webview  # noqa: E402

from app.bridge import Api  # noqa: E402
from app.paths import WEB_DIR  # noqa: E402

falhas: list[str] = []
janela = None
concluiu = False  # só vira True se o roteiro inteiro rodar

LOTE = """P: Rio mais extenso do mundo?
a) Nilo
b) Amazonas *
c) Yangtze
E: Medicoes de 2007 apontaram o Amazonas como o mais extenso.
---
P: O que e fuso horario?
R: Faixa que adota a mesma hora legal.
E: A Terra gira 15 graus por hora.
"""


def js(codigo: str):
    return janela.evaluate_js(codigo)


def esperar(expressao: str, descricao: str, segundos: float = 12.0):
    """Espera uma expressão JS virar verdadeira — a interface é toda assíncrona."""
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


def tecla(k: str) -> None:
    js(f"document.dispatchEvent(new KeyboardEvent('keydown', {{key: {json.dumps(k)}, bubbles: true}}))")


def roteiro(_janela=None) -> None:
    global concluiu
    try:
        secao("interface")
        esperar("!!window.pywebview && !!document.querySelector('#deck-list')", "interface carregou")
        # captura qualquer erro de JS ou violação da CSP daqui em diante
        js("window.__erros = [];"
           "addEventListener('error', e => __erros.push('erro: ' + e.message));"
           "addEventListener('unhandledrejection', e => __erros.push('promessa: ' + (e.reason && e.reason.message)));"
           "addEventListener('securitypolicyviolation', e => __erros.push('CSP: ' + e.violatedDirective + ' ' + e.blockedURI));")
        esperar("document.querySelector('#home-summary').children.length > 0", "resumo carregou")
        checar(js("document.querySelectorAll('.deck').length") == 0, "começa sem baralhos")
        checar(bool(js("!!document.querySelector('.onboarding')")), "mostra a tela de primeiros passos")
        checar(js("getComputedStyle(document.body).backgroundColor") != "rgba(0, 0, 0, 0)", "estilos carregaram")
        checar(js("!!document.querySelector('.sidebar')"), "barra lateral existe")
        checar(js("document.querySelector('#page-date').textContent.length") > 5, "cabeçalho mostra a data")

        secao("criar baralho")
        js("document.querySelector('#btn-new-deck').click()")
        js("document.querySelector('#deck-name').value = 'Geografia'")
        js("document.querySelector('#form-deck').requestSubmit()")
        esperar("document.querySelectorAll('.deck').length === 1", "baralho apareceu na lista")
        checar(js("document.querySelector('.deck h3').textContent") == "Geografia", "nome do baralho")
        esperar("document.querySelectorAll('[data-side-deck]').length === 1", "baralho aparece na barra lateral")
        checar(bool(js("!!document.querySelector('.deck .stack')")), "barra de progresso do baralho")

        secao("importar (leitura de arquivo)")
        js("document.querySelector('[data-view=import]').click()")
        # CSV salvo pelo Excel vem em cp1252, não em UTF-8: acentos não podem quebrar
        js("lerArquivo(new File([new Uint8Array([0x70,0x65,0x72,0x67,0x75,0x6e,0x74,0x61,0x2c,0x72,0x65,0x73,0x70,0x6f,0x73,0x74,0x61,0x0a,0x41,0xe7,0xe3,0x6f,0x3f,0x2c,0x4e,0xe3,0x6f])], 'excel.csv'))")
        esperar("document.querySelector('#import-content').value.includes('Ação')", "CSV em cp1252 lido com acentos")
        checar(js("document.querySelector('#import-format').value") == "csv", "formato escolhido pela extensão")
        js("document.querySelector('#import-content').value = ''; document.querySelector('#import-preview').innerHTML = ''")

        js(f"lerArquivo(new File([{json.dumps(LOTE)}], 'lote.txt'))")
        esperar("document.querySelectorAll('#import-preview .card-item').length === 2", "pré-visualização mostrou 2 questões")
        js("document.querySelector('#btn-do-import').click()")
        esperar("document.querySelector('#toast').textContent.includes('importadas')", "toast de importação")

        secao("estudar (teclado)")
        js("document.querySelector('[data-view=home]').click()")
        esperar("document.querySelector('[data-study]') && !document.querySelector('[data-study]').disabled",
                "botão Estudar liberado")
        js("document.querySelector('[data-study]').click()")
        esperar("document.querySelectorAll('.option').length >= 2 || document.querySelector('#btn-reveal')",
                "primeira questão apareceu")
        checar(js("document.body.classList.contains('studying')"), "modo foco liga (barra lateral recolhe)")
        esperar("document.querySelector('.sidebar').getBoundingClientRect().width < 2",
                "lateral recolhe até zerar a largura", 4)
        checar(js("document.querySelector('#study-progress-bar').style.width") == "0%", "barra de progresso começa em 0%")

        if js("document.querySelectorAll('.option').length >= 2"):
            print("   (questão de múltipla escolha)", flush=True)
            tecla("a")  # letra A escolhe a primeira alternativa (errada: Nilo)
            esperar("!!document.querySelector('.feedback')", "feedback apareceu")
            checar(js("document.querySelectorAll('.option.correct').length") == 1, "correta marcada em verde")
            checar(js("document.querySelectorAll('.option.wrong').length") == 1, "errada marcada em vermelho")
            checar(bool(js("document.querySelector('.feedback').textContent.includes('Amazonas')")), "mostra a resposta certa")
            checar(bool(js("document.querySelector('.feedback').textContent.includes('2007')")), "mostra a explicação do erro")
            checar(bool(js("document.querySelector('[data-grade]').textContent.includes('volta em')")), "mostra quando volta")
            checar(bool(js("!!document.querySelector('.grade.sugerida')")), "destaca a opção sugerida")
            tecla("Enter"); tecla("Enter"); tecla("Enter")  # três Enter seguidos: só uma nota pode ser gravada
            esperar("document.querySelector('#toast').textContent.includes('Volta em')", "toast do agendamento")
            checar(bool(js("document.querySelector('#study-score').textContent.includes('✘ 1')")), "placar mostra 1 erro")

        secao("flashcard (teclado)")
        esperar("!!document.querySelector('#btn-reveal')", "flashcard apareceu")
        tecla(" ")
        esperar("!!document.querySelector('.flash-back')", "verso revelado")
        checar(js("document.querySelectorAll('[data-grade]').length") == 4, "quatro notas disponíveis")
        tecla("3")  # 3 = Bom
        esperar("!!document.querySelector('.empty-study')", "sessão terminou")
        checar(bool(js("document.querySelector('.empty-study').textContent.includes('Sessão concluída')")), "mostra 'Sessão concluída'")
        checar(js("document.querySelectorAll('.session-stats .stat').length") == 4, "resumo da sessão com 4 números")
        checar(js("document.querySelector('#study-progress-bar').style.width") == "100%", "barra de progresso em 100%")

        with sqlite3.connect(BANCO_TESTE / "studyia.db") as c:
            n = c.execute("select count(*) from review").fetchone()[0]
        checar(n == 2, f"exatamente 2 revisões gravadas apesar dos Enter repetidos (gravadas: {n})")

        secao("progresso")
        js("document.querySelector('[data-view=stats]').click()")
        esperar("!document.body.classList.contains('studying')", "modo foco desliga ao sair do estudo")
        esperar("!!document.querySelector('#stats-body .stat')", "estatísticas carregaram")
        checar(js("document.querySelectorAll('.heat i').length") == 84, "mapa de atividade com 84 dias")
        checar(js("document.querySelectorAll('.heat i.l1, .heat i.l2').length") >= 1, "hoje aparece pintado no mapa")
        checar(bool(js("document.querySelector('#stats-body').textContent.includes('revisões feitas')")), "mostra revisões feitas")

        secao("gerar com IA (sem chave)")
        js("document.querySelector('[data-view=ai]').click()")
        esperar("!!document.querySelector('#ai-key')", "pede a chave do Gemini")
        checar(js("document.querySelector('#ai-key').type") == "password", "campo da chave é do tipo senha")
        checar(bool(js("document.querySelector('#view-ai').textContent.includes('enviado ao Google')")), "avisa que o texto vai para o Google")
        js("document.querySelector('#ai-material').value = 'Lei 8.112/90'; "
           "document.querySelector('#ai-material').dispatchEvent(new Event('input'))")
        checar(bool(js("document.querySelector('#ai-count').textContent.includes('de 40.000')")),
               "contador mostra o limite de 40.000 caracteres")
        js("document.querySelector('#btn-generate').click()")
        esperar("document.querySelector('#toast').classList.contains('bad')", "gerar sem chave mostra erro amigável")
        esperar("document.querySelector('#ai-error .notice.error') !== null", "o erro fica visível na página (não só no aviso de 3 s)")
        time.sleep(3.6)   # o aviso flutuante já sumiu; o erro na página tem que continuar
        checar(bool(js("document.querySelector('#ai-error').textContent.includes('chave')")),
               "erro continua na tela depois que o aviso some")
        # material acima do limite: o botão trava e nenhum pedido é feito
        js("document.querySelector('#ai-material').value = 'x'.repeat(40001); "
           "document.querySelector('#ai-material').dispatchEvent(new Event('input'))")
        checar(js("document.querySelector('#btn-generate').disabled"), "material acima do limite trava o botão")
        checar(bool(js("document.querySelector('#ai-count').classList.contains('over')")), "contador fica vermelho acima do limite")
        js("document.querySelector('#ai-material').value = ''; "
           "document.querySelector('#ai-material').dispatchEvent(new Event('input'))")
        checar(not js("document.querySelector('#btn-generate').disabled"), "botão volta ao normal com material dentro do limite")

        secao("questões (gerenciar)")
        js("document.querySelector('[data-view=manage]').click()")
        esperar("document.querySelectorAll('#card-list .card-item').length === 2", "lista as 2 questões")
        js("document.querySelector('[data-filter=new]').click()")
        esperar("document.querySelectorAll('#card-list .card-item').length === 0", "filtro 'Novas' esvazia (as 2 já foram estudadas)")
        js("document.querySelector('[data-filter=learning]').click()")
        esperar("document.querySelectorAll('#card-list .card-item').length === 2", "filtro 'Aprendendo' mostra as 2")
        js("document.querySelector('[data-filter=all]').click()")
        js("document.querySelector('[data-susp]').click()")
        esperar("document.querySelectorAll('.card-item.paused').length === 1", "pausar uma questão")
        js("document.querySelector('[data-susp]').click()")
        esperar("document.querySelectorAll('.card-item.paused').length === 0", "retomar a questão")
        js("document.querySelector('#manage-search').value = 'fuso'; "
           "document.querySelector('#manage-search').dispatchEvent(new Event('input'))")
        esperar("document.querySelectorAll('#card-list .card-item').length === 1", "busca filtra")
        js("document.querySelector('#manage-search').value = ''; "
           "document.querySelector('#manage-search').dispatchEvent(new Event('input'))")

        secao("navegação pela barra lateral")
        js("document.querySelector('[data-view=home]').click()")
        esperar("!!document.querySelector('[data-side-deck]')", "lateral listou o baralho")
        js("document.querySelector('[data-side-deck]').click()")
        esperar("document.querySelector('#view-manage').classList.contains('active')", "clicar no baralho abre Questões")
        esperar("!!document.querySelector('.deck-page-head')", "cabeçalho do baralho aparece")
        checar(bool(js("document.querySelector('.deck-page-head').textContent.includes('Estudar')")),
               "cabeçalho traz o botão Estudar")

        secao("ajustes")
        js("document.querySelector('[data-view=settings]').click()")
        esperar("document.querySelector('#about-version').textContent.includes('StudyIA')", "ajustes carregaram")
        js("document.querySelector('[data-theme-choice=claro]').click()")
        esperar("document.documentElement.dataset.theme === 'light'", "tema claro aplicado")
        # luminância em vez da cor exata: a paleta pode mudar sem quebrar o teste
        luz = js("(() => { const [r,g,b] = getComputedStyle(document.body).backgroundColor"
                 r".match(/\d+/g).map(Number); return 0.2126*r + 0.7152*g + 0.0722*b; })()")
        checar(luz and luz > 200, f"fundo do tema claro é claro (luminância {luz})")
        js("document.querySelector('[data-theme-choice=escuro]').click()")
        esperar("document.documentElement.dataset.theme === 'dark'", "tema escuro aplicado")
        escuro = js("(() => { const [r,g,b] = getComputedStyle(document.body).backgroundColor"
                    r".match(/\d+/g).map(Number); return 0.2126*r + 0.7152*g + 0.0722*b; })()")
        checar(escuro is not None and escuro < 40, f"fundo do tema escuro é escuro (luminância {escuro})")
        js("document.querySelector('[data-theme-choice=auto]').click()")
        esperar("!document.documentElement.dataset.theme", "voltou para automático")
        time.sleep(0.4)
        with sqlite3.connect(BANCO_TESTE / "studyia.db") as c:
            tema = c.execute("select value from setting where key='theme'").fetchone()
        checar(tema and tema[0] == "auto", "tema foi salvo no banco")

        secao("modais (renomear e excluir)")
        js("document.querySelector('[data-view=home]').click()")
        esperar("document.querySelectorAll('.deck').length === 1", "voltou aos baralhos")
        js("document.querySelector('[data-rename]').click()")
        esperar("document.querySelector('#modal').open", "modal de renomear abriu")
        js("document.querySelector('#modal-input').value = 'Geografia física'")
        js("document.querySelector('#modal-ok').click()")
        esperar("document.querySelector('.deck h3').textContent === 'Geografia física'", "baralho renomeado")

        js("document.querySelector('#btn-new-deck').click()")
        js("document.querySelector('#deck-name').value = 'Temporário'")
        js("document.querySelector('#form-deck').requestSubmit()")
        esperar("document.querySelectorAll('.deck').length === 2", "segundo baralho criado")
        js("[...document.querySelectorAll('[data-del-deck]')].pop().click()")
        esperar("document.querySelector('#modal').open", "modal de excluir abriu")
        js("document.querySelector('#modal-cancel').click()")
        time.sleep(0.5)
        checar(js("document.querySelectorAll('.deck').length") == 2, "cancelar não exclui")
        js("[...document.querySelectorAll('[data-del-deck]')].pop().click()")
        esperar("document.querySelector('#modal').open", "modal reabriu")
        js("document.querySelector('#modal-ok').click()")
        esperar("document.querySelectorAll('.deck').length === 1", "confirmar exclui")

        secao("política de segurança (CSP) realmente bloqueia")
        # sem estes dois testes, "nenhuma violação" também seria o resultado de uma CSP desligada
        js("window.__inj = 'nao-rodou';"
           "const s = document.createElement('script'); s.textContent = \"window.__inj = 'RODOU'\";"
           "document.body.appendChild(s);")
        time.sleep(0.3)
        checar(js("window.__inj") == "nao-rodou", "script injetado no HTML é bloqueado")
        js("window.__rede = 'pendente';"
           "fetch('https://example.com/').then(() => window.__rede = 'ABRIU').catch(() => window.__rede = 'bloqueado');")
        esperar("window.__rede !== 'pendente'", "conexão externa terminou de ser tentada", 8)
        checar(js("window.__rede") == "bloqueado", "conexão de rede pela página é bloqueada")
        js("window.__erros = []")  # as violações acima eram esperadas

        secao("saúde do JavaScript")
        erros = js("window.__erros")
        checar(not erros, f"nenhum erro de JS nem violação de CSP durante o teste {erros or ''}")
        concluiu = True
    except Exception as exc:
        falhas.append(f"erro inesperado: {exc}")
    finally:
        janela.destroy()


def main() -> int:
    global janela
    # Mesmo carregamento do programa real (file://, sem servidor HTTP).
    janela = webview.create_window("StudyIA (teste)", (WEB_DIR / "index.html").as_uri(),
                                   js_api=Api(), width=1100, height=820)
    janela.events.closed += lambda: print("  [evento] a janela foi fechada", flush=True)
    webview.start(roteiro, janela)

    print("\n" + "=" * 52)
    if not concluiu and not falhas:
        falhas.append("o roteiro nao chegou ao fim (a janela fechou ou a thread quebrou)")
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
