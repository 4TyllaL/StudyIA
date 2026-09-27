"""Modo debate: a IA lê o material, acha os temas centrais e debate com o estudante.

Três pedidos diferentes ao Gemini, cada um do tamanho do que precisa:

* **temas** — o único que leva o material inteiro. Devolve os temas com resumo, uma tese
  para debater e os pontos-chave de cada um.
* **turno** — cada fala do debate. Leva só o tema escolhido (resumo + pontos-chave) e as
  últimas mensagens, nunca o material de novo: um debate longo não pode ficar mais caro a
  cada rodada.
* **resumo** — no fim: o que o estudante dominou, o que revisar e questões para o baralho,
  focadas nas falhas que apareceram na conversa.

A leitura de arquivos (PDF, Word, texto) é local; só o texto extraído vai para o Google.
"""
from __future__ import annotations

import base64
import io
import re
import zipfile
from xml.etree import ElementTree

from . import ai

MAX_MATERIAL = 120_000        # caracteres; só o pedido de temas leva o material
MAX_ARQUIVO = 15 * 1024 * 1024
MAX_MENSAGEM = 2_000          # uma resposta do estudante
MENSAGENS_NO_PEDIDO = 12      # as mais recentes; o resto da conversa fica só no banco


class ErroDeLeitura(Exception):
    """Arquivo que não deu para ler, com mensagem pronta para a tela."""


# ------------------------------------------------------------------ leitura de arquivo

def ler_arquivo(nome: str, dados_b64: str) -> dict:
    """Extrai o texto de .pdf, .docx, .txt ou .md. Nada sai do computador aqui."""
    try:
        dados = base64.b64decode(dados_b64 or "", validate=True)
    except ValueError:
        raise ErroDeLeitura("Não consegui receber o arquivo. Tente de novo.") from None
    if not dados:
        raise ErroDeLeitura("O arquivo está vazio.")
    if len(dados) > MAX_ARQUIVO:
        raise ErroDeLeitura("Arquivo grande demais (limite de 15 MB).")

    extensao = nome.rsplit(".", 1)[-1].lower() if "." in nome else ""
    if extensao == "pdf":
        texto = _ler_pdf(dados)
    elif extensao == "docx":
        texto = _ler_docx(dados)
    elif extensao in ("txt", "md", "markdown"):
        texto = _decodificar(dados)
    elif extensao == "doc":
        raise ErroDeLeitura("Arquivos .doc (Word antigo) não são lidos. No Word, use "
                            "Salvar como → Documento do Word (.docx).")
    else:
        raise ErroDeLeitura("Formato não suportado. Use PDF, Word (.docx), .txt ou .md.")

    texto = _limpar(texto)
    if len(texto) < 40:
        raise ErroDeLeitura("Não encontrei texto nesse arquivo.")
    return {"nome": nome, "texto": texto, "caracteres": len(texto)}


def _decodificar(dados: bytes) -> str:
    try:
        return dados.decode("utf-8-sig")
    except UnicodeDecodeError:
        return dados.decode("cp1252", errors="replace")   # salvo pelo Bloco de Notas antigo


def _ler_pdf(dados: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        leitor = PdfReader(io.BytesIO(dados))
        if leitor.is_encrypted and not leitor.decrypt(""):
            raise ErroDeLeitura("Esse PDF está protegido por senha.")
        texto = "\n\n".join((pagina.extract_text() or "") for pagina in leitor.pages)
    except ErroDeLeitura:
        raise
    except (PdfReadError, ValueError, KeyError, OSError):
        raise ErroDeLeitura("Não consegui abrir esse PDF — ele pode estar corrompido.") from None
    if len(texto.strip()) < 40:
        raise ErroDeLeitura("Esse PDF parece ser escaneado (só imagem), então não há texto para "
                            "ler. Se tiver a versão em texto, use-a; ou copie o conteúdo e cole.")
    return texto


def _ler_docx(dados: bytes) -> str:
    """Um .docx é um zip; o texto fica em word/document.xml. Sem dependência extra."""
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        with zipfile.ZipFile(io.BytesIO(dados)) as pacote:
            raiz = ElementTree.fromstring(pacote.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError):
        raise ErroDeLeitura("Não consegui abrir esse arquivo do Word.") from None
    paragrafos = []
    for p in raiz.iter(f"{w}p"):
        partes = []
        for no in p.iter():
            if no.tag == f"{w}t" and no.text:
                partes.append(no.text)
            elif no.tag == f"{w}tab":
                partes.append("\t")
            elif no.tag in (f"{w}br", f"{w}cr"):
                partes.append("\n")
        paragrafos.append("".join(partes))
    return "\n".join(paragrafos)


def _limpar(texto: str) -> str:
    texto = texto.replace("\r\n", "\n").replace("\x00", "")
    texto = re.sub(r"[ \t]+\n", "\n", texto)
    return re.sub(r"\n{3,}", "\n\n", texto).strip()


# ---------------------------------------------------------------------------- temas

SCHEMA_TEMAS = {
    "type": "object",
    "properties": {
        "resumo": {"type": "string", "description": "Do que trata o material, em 2 ou 3 frases"},
        "temas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "titulo": {"type": "string", "description": "Nome curto do tema (até 8 palavras)"},
                    "resumo": {"type": "string", "description": "O que o material diz sobre o tema, em 2 a 4 frases"},
                    "tese": {"type": "string", "description": "Uma afirmação discutível sobre o tema, para debater"},
                    "pontos": {"type": "array", "items": {"type": "string"},
                               "description": "3 a 6 fatos, regras ou argumentos do material sobre o tema"},
                },
                "required": ["titulo", "resumo", "tese", "pontos"],
            },
        },
    },
    "required": ["resumo", "temas"],
}

SYSTEM_TEMAS = """Você é um professor que prepara debates de estudo em português do Brasil.

Leia o material e identifique de 3 a 6 temas CENTRAIS — os que mais importam para entender o
conteúdo (e para uma prova), não detalhes soltos. Para cada tema:
- resumo: o que o material diz, fiel ao texto;
- tese: uma afirmação discutível que obrigue o estudante a argumentar, a partir do que o
  material diz (ex.: "A estabilidade do servidor protege mais a administração do que o
  próprio servidor"). Nada de perguntas nem afirmações óbvias;
- pontos: fatos, regras, exceções e argumentos do material que servem para defender ou
  atacar a tese.
Não invente nada que não esteja no material. Responda apenas com o JSON no formato pedido.
"""


def temas(material: str, api_key: str = "", model: str = "") -> dict:
    material = (material or "").strip()
    if not material:
        raise RuntimeError("Escolha um arquivo ou cole o material primeiro.")
    if len(material) > MAX_MATERIAL:
        raise RuntimeError(
            f"O material tem {_milhar(len(material))} caracteres; o limite é "
            f"{_milhar(MAX_MATERIAL)}. Use um capítulo ou parte do material por vez.")
    dados = ai.pedir_json(system=SYSTEM_TEMAS, entrada="--- MATERIAL ---\n" + material,
                          schema=SCHEMA_TEMAS, max_tokens=6_000, api_key=api_key, model=model)
    lista = []
    for t in dados.get("temas") or []:
        if not isinstance(t, dict) or not str(t.get("titulo", "")).strip():
            continue
        lista.append({
            "titulo": str(t.get("titulo", "")).strip(),
            "resumo": str(t.get("resumo", "")).strip(),
            "tese": str(t.get("tese", "")).strip(),
            "pontos": [str(p).strip() for p in (t.get("pontos") or []) if str(p).strip()][:8],
        })
    if not lista:
        raise RuntimeError("O Gemini não encontrou temas nesse material. Tente um texto mais completo.")
    return {"resumo": str(dados.get("resumo", "")).strip(), "temas": lista[:6]}


# ---------------------------------------------------------------------------- turno

SCHEMA_TURNO = {
    "type": "object",
    "properties": {
        "fala": {"type": "string", "description": "O que o debatedor diz agora"},
        "lacuna": {"type": "string",
                   "description": "Erro ou omissão importante na última resposta do estudante, em "
                                  "uma frase; vazio se não houver"},
    },
    "required": ["fala", "lacuna"],
}

SYSTEM_TURNO = """Você é um debatedor socrático que ajuda um estudante a pensar sobre um tema de
estudo, em português do Brasil.

Como debater:
- Desafie. Tome o lado contrário ao do estudante, peça justificativa, traga uma exceção ou um
  caso concreto que complique o argumento dele. Não concorde por educação.
- Se o estudante errar um fato, diga que está errado e qual é o certo, com base nos pontos-chave.
- Se ele acertar, reconheça em poucas palavras e aprofunde com uma pergunta mais difícil.
- Use só o que está no tema e nos pontos-chave; não invente leis, números ou fatos.
- Sua fala será LIDA EM VOZ ALTA: escreva como quem fala, em até 90 palavras, sem markdown,
  listas, emojis ou abreviações.
- Termine SEMPRE com uma pergunta ou provocação para o estudante responder.

Em "lacuna", anote em uma frase o erro conceitual ou a omissão importante da última resposta
do estudante (vira material de revisão depois). Se não houver, deixe vazio.
Responda apenas com o JSON no formato pedido.
"""


def turno(tema: dict, mensagens: list[dict], api_key: str = "", model: str = "") -> dict:
    """Próxima fala da IA. `mensagens` = [{"papel": "ia"|"estudante", "texto": ...}]."""
    recentes = mensagens[-MENSAGENS_NO_PEDIDO:]
    partes = [_descrever_tema(tema), ""]
    if not recentes:
        partes.append("A conversa ainda não começou. Abra o debate: apresente a tese de forma "
                      "provocativa, em poucas frases, e faça a primeira pergunta.")
    else:
        if len(mensagens) > len(recentes):
            partes.append("(Mensagens mais antigas da conversa foram omitidas.)")
        partes.append("CONVERSA ATÉ AQUI:")
        for m in recentes:
            quem = "Debatedor" if m.get("papel") == "ia" else "Estudante"
            partes.append(f"{quem}: {m.get('texto', '')}")
        partes.append("\nResponda à última mensagem do estudante.")
    dados = ai.pedir_json(system=SYSTEM_TURNO, entrada="\n".join(partes), schema=SCHEMA_TURNO,
                          max_tokens=1_500, api_key=api_key, model=model)
    fala = str(dados.get("fala", "")).strip()
    if not fala:
        raise RuntimeError("O Gemini respondeu sem nenhuma fala. Tente de novo.")
    return {"fala": fala, "lacuna": str(dados.get("lacuna", "")).strip()}


# --------------------------------------------------------------------------- resumo

SCHEMA_RESUMO = {
    "type": "object",
    "properties": {
        "dominou": {"type": "array", "items": {"type": "string"},
                    "description": "O que o estudante mostrou que entende (1 frase cada)"},
        "revisar": {"type": "array", "items": {"type": "string"},
                    "description": "O que ele errou, confundiu ou não soube defender (1 frase cada)"},
        "cards": ai.RESPONSE_SCHEMA["properties"]["cards"],
    },
    "required": ["dominou", "revisar", "cards"],
}

SYSTEM_RESUMO = """Você avalia um debate de estudo que acabou de terminar, em português do Brasil.

- dominou: o que o estudante demonstrou entender, com base no que ele de fato escreveu.
- revisar: o que ele errou, confundiu, deixou de considerar ou não conseguiu defender.
- cards: de 3 a 6 questões de estudo focadas no que está em "revisar" (se não houver nada,
  nos pontos mais importantes do tema). Siga estas regras nas questões:
  quiz = 4 alternativas plausíveis, answer_index 0-based, answer_text vazio;
  flash = options vazio, answer_index -1, answer_text com o verso;
  a explicação diz por que a correta está certa e qual é a pegadinha, em 2 ou 3 frases.
Seja honesto: se o estudante respondeu pouco, diga isso em "revisar".
Use só o que está no tema e nos pontos-chave. Responda apenas com o JSON no formato pedido.
"""


def resumo(tema: dict, mensagens: list[dict], api_key: str = "", model: str = "") -> dict:
    conversa = "\n".join(
        f"{'Debatedor' if m.get('papel') == 'ia' else 'Estudante'}: {m.get('texto', '')}"
        for m in mensagens[-40:])
    lacunas = [m["lacuna"] for m in mensagens if m.get("lacuna")]
    entrada = _descrever_tema(tema) + "\n\nCONVERSA:\n" + conversa
    if lacunas:
        entrada += "\n\nFALHAS ANOTADAS DURANTE O DEBATE:\n" + "\n".join(f"- {x}" for x in lacunas)
    dados = ai.pedir_json(system=SYSTEM_RESUMO, entrada=entrada, schema=SCHEMA_RESUMO,
                          max_tokens=8_000, api_key=api_key, model=model)
    cards = [c for c in (ai._to_card(g) for g in ai._validar(dados.get("cards") or [])) if c]
    return {
        "dominou": [str(x).strip() for x in (dados.get("dominou") or []) if str(x).strip()],
        "revisar": [str(x).strip() for x in (dados.get("revisar") or []) if str(x).strip()],
        "cards": cards,
    }


# ------------------------------------------------------------------------- apoio

def _descrever_tema(tema: dict) -> str:
    pontos = "\n".join(f"- {p}" for p in tema.get("pontos") or [])
    return (f"TEMA: {tema.get('titulo', '')}\n"
            f"RESUMO: {tema.get('resumo', '')}\n"
            f"TESE PARA DEBATER: {tema.get('tese', '')}\n"
            f"PONTOS-CHAVE DO MATERIAL:\n{pontos}")


def _milhar(x: int) -> str:
    return f"{x:,}".replace(",", ".")
