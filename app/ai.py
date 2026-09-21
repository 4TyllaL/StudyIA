"""Geração de questões com a API do Gemini (Google), com saída estruturada.

A chave pode vir das variáveis de ambiente GEMINI_API_KEY / GOOGLE_API_KEY ou da
tabela `setting` do SQLite (salva pela própria interface).

Princípios que este módulo segue (cada um corrige um problema real encontrado):

* **Um pedido por clique.** O SDK, por padrão, repete o pedido inteiro até 4 vezes em erro
  de servidor (429/5xx) e não tem timeout. Aqui as retentativas são desligadas e há timeout:
  se falhar, o usuário vê o motivo e decide se tenta de novo.
* **Pedido do tamanho certo.** O teto de tokens de saída acompanha a quantidade pedida, e o
  material tem limite (com aviso claro, sem cortar em silêncio).
* **Nunca "vazio" sem explicação.** A resposta traz um `status`; resposta cortada, falha
  do modelo e bloqueio viram mensagens diferentes — e o que der para aproveitar de uma
  resposta cortada é aproveitado, em vez de jogar fora o que já foi gerado (e cobrado).
"""
from __future__ import annotations

import json
import os
import time
from typing import Literal

from pydantic import BaseModel, ValidationError

DEFAULT_MODEL = os.environ.get("STUDYIA_MODEL", "gemini-3.8-flash")
MAX_QUESTIONS = 20
MAX_MATERIAL = 40_000          # caracteres; acima disso o pedido fica grande demais
TIMEOUT_S = float(os.environ.get("STUDYIA_TIMEOUT_S", "120"))
CACHE_MODELOS_S = 600

# Usado quando não dá para consultar a lista de modelos da conta.
FALLBACK_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
]


class GeneratedCard(BaseModel):
    kind: Literal["quiz", "flash"]
    question: str
    options: list[str] = []
    answer_index: int = -1
    answer_text: str = ""
    explanation: str = ""
    tags: str = ""


class GeneratedSet(BaseModel):
    cards: list[GeneratedCard]


# Schema explícito (sem $ref) — é o que a API aceita em response_format.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["quiz", "flash"],
                             "description": "quiz = múltipla escolha; flash = frente/verso"},
                    "question": {"type": "string", "description": "Enunciado ou frente do card"},
                    "options": {"type": "array", "items": {"type": "string"},
                                "description": "4 alternativas no quiz; lista vazia no flashcard"},
                    "answer_index": {"type": "integer",
                                     "description": "Índice 0-based da correta; -1 no flashcard"},
                    "answer_text": {"type": "string",
                                    "description": "Verso do flashcard; vazio no quiz"},
                    "explanation": {"type": "string",
                                    "description": "Por que a correta está certa e onde está a pegadinha"},
                    "tags": {"type": "string", "description": "1 a 3 tópicos separados por vírgula"},
                },
                "required": ["kind", "question", "options", "answer_index",
                             "answer_text", "explanation", "tags"],
            },
        }
    },
    "required": ["cards"],
}

SYSTEM = """Você é um professor experiente que elabora questões de estudo em português do Brasil.

Regras:
- Cada questão testa UM conceito específico e é respondível a partir do material dado.
- Em quiz, gere exatamente 4 alternativas plausíveis. Os distratores devem refletir erros
  reais que estudantes cometem (confusão de conceitos, troca de prazos/valores, inversão de
  causa e efeito), nunca alternativas absurdas ou obviamente erradas.
- Varie a posição da alternativa correta entre as questões.
- A explicação é a parte mais importante: diga por que a correta está certa, cite a regra,
  artigo, fórmula ou princípio envolvido, e alerte sobre a pegadinha mais provável.
  Escreva de 2 a 3 frases, direto ao ponto, sem repetir o enunciado.
- Não invente fatos. Se o material do usuário for curto, prefira menos questões a inventar conteúdo.
- Não numere as questões nem escreva "Alternativa A" dentro do texto das opções.
- Responda apenas com o JSON no formato pedido.
"""


def resolve_api_key(stored: str = "") -> str:
    return (os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or stored or "").strip()


def key_from_env() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


def _client(api_key: str):
    key = resolve_api_key(api_key)
    if not key:
        raise RuntimeError(
            "Sem chave da API. Defina GEMINI_API_KEY ou salve a chave na aba Gerar com IA."
        )
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:  # pragma: no cover - dependência declarada no requirements
        raise RuntimeError("Pacote `google-genai` não instalado (pip install google-genai).") from exc

    # `attempts=0` NÃO desliga a repetição (o SDK trata 0 como 1 e ainda manda 2 pedidos);
    # o que desliga é limitar a repetição a um código HTTP que o Gemini nunca devolve.
    # Medido: 503 e 429 passam a gerar exatamente 1 pedido (antes eram 4).
    opcoes = types.HttpOptions(
        timeout=int(TIMEOUT_S * 1000),                        # milissegundos
        retry_options=types.HttpRetryOptions(attempts=1, http_status_codes=[599]),
    )
    return genai.Client(api_key=key, http_options=opcoes)


_cache_modelos: dict[str, tuple[float, list[str]]] = {}


def list_models(api_key: str = "") -> list[str]:
    """Modelos de texto da conta; cai no fallback se a listagem falhar.

    O resultado fica em memória por alguns minutos: abrir a aba de IA várias vezes não
    deve gerar uma consulta à API a cada vez.
    """
    chave = resolve_api_key(api_key)
    guardado = _cache_modelos.get(chave)
    if guardado and time.time() - guardado[0] < CACHE_MODELOS_S:
        return guardado[1]
    try:
        client = _client(api_key)
        nomes = []
        for m in client.models.list():
            nome = (getattr(m, "name", "") or "").removeprefix("models/")
            if not nome.startswith("gemini"):
                continue
            if any(x in nome for x in ("embedding", "image", "tts", "live", "veo", "aqa")):
                continue
            nomes.append(nome)
        lista = sorted(set(nomes)) or FALLBACK_MODELS
        if nomes:
            _cache_modelos[chave] = (time.time(), lista)
        return lista
    except Exception:
        return FALLBACK_MODELS


def _build_prompt(material: str, n: int, kind: str, difficulty: str, notes: str) -> str:
    if kind == "flash":
        tipo = "apenas flashcards (kind='flash', frente/verso, options vazio, answer_index -1)"
    elif kind == "mixed":
        tipo = "cerca de 70% quiz de múltipla escolha e 30% flashcards"
    else:
        tipo = "apenas questões de múltipla escolha (kind='quiz', 4 alternativas, answer_text vazio)"

    partes = [
        f"Gere {n} questões de estudo a partir do material abaixo.",
        f"Tipo: {tipo}.",
        f"Nível de dificuldade: {difficulty}.",
    ]
    if notes.strip():
        partes.append(f"Instruções extras do estudante: {notes.strip()}")
    partes.append("\n--- MATERIAL ---\n" + material.strip())
    return "\n".join(partes)


def _config_geracao(model: str, n: int) -> dict:
    """Teto de saída proporcional ao pedido e raciocínio mínimo.

    Gerar questões a partir de um texto que já está no pedido não precisa de raciocínio
    longo — e o raciocínio conta contra o teto de saída: com teto alto e raciocínio
    "solto", o modelo gasta tempo (e tokens cobrados) pensando antes de escrever.
    `thinking_level` só existe nos modelos 3.x; enviá-lo a um 2.5 daria erro.
    """
    config = {"max_output_tokens": min(16_000, 3_000 + 700 * n)}
    if model.startswith("gemini-3"):
        # "minimal" é dos modelos flash; os Pro só aceitam níveis mais altos, então usam "low".
        # (Baseado na documentação — não pude confirmar com a API real, por isso o "low"
        # como escolha conservadora fora dos flash.)
        config["thinking_level"] = "minimal" if "flash" in model else "low"
    return config


# ------------------------------------------------------------ leitura da resposta

def _texto_da_resposta(interaction) -> str:
    """Junta o texto que o modelo escreveu (ignora o que foi só 'pensamento')."""
    texto = (getattr(interaction, "output_text", "") or "").strip()
    if texto:
        return texto
    partes = []
    for passo in getattr(interaction, "steps", None) or []:
        if getattr(passo, "type", "") != "model_output":
            continue
        for bloco in getattr(passo, "content", None) or []:
            if getattr(bloco, "type", "") == "text" and getattr(bloco, "text", ""):
                partes.append(bloco.text)
    return "".join(partes).strip()


def _recuperar_parcial(texto: str) -> list[dict]:
    """Tira os cards COMPLETOS de um JSON que foi cortado no meio.

    Uma resposta cortada em `{"cards":[{...},{...},{"kind":"qu` ainda tem as duas primeiras
    questões inteiras; descartar tudo seria pagar pela geração e ficar sem nada.
    """
    ini = texto.find("[")
    if ini < 0:
        return []
    decodificador, pos, achados = json.JSONDecoder(), ini + 1, []
    while pos < len(texto):
        while pos < len(texto) and texto[pos] in " \r\n\t,":
            pos += 1
        if pos >= len(texto) or texto[pos] != "{":
            break
        try:
            obj, pos = decodificador.raw_decode(texto, pos)
        except ValueError:
            break  # objeto cortado: para aqui
        achados.append(obj)
    return achados


def _validar(objetos: list[dict]) -> list[GeneratedCard]:
    validos = []
    for obj in objetos:
        try:
            validos.append(GeneratedCard.model_validate(obj))
        except ValidationError:
            continue
    return validos


def _mensagem_de_status(interaction, texto: str) -> str:
    """Explica por que uma resposta não terminou bem, em vez de um 'veio vazia' genérico."""
    status = getattr(interaction, "status", "") or ""
    erros = getattr(interaction, "errors", None) or []
    detalhe = "; ".join(getattr(e, "message", "") or "" for e in erros).strip("; ")

    if status == "incomplete":
        return ("A resposta foi cortada antes de terminar (limite de tamanho). "
                "Peça menos questões por vez ou use um material menor.")
    if status == "failed":
        return f"O Gemini não conseguiu gerar a resposta{': ' + detalhe if detalhe else '.'}"
    if status in ("cancelled", "budget_exceeded"):
        return f"O Gemini interrompeu a geração ({status})."
    if not texto:
        return ("O Gemini respondeu sem nenhum conteúdo — pode ter sido bloqueado pelos filtros "
                "de segurança. Tente reescrever o material.")
    return "A resposta não veio no formato esperado. Tente gerar menos questões por vez."


def generate(
    material: str,
    n: int = 10,
    kind: str = "quiz",
    difficulty: str = "médio",
    notes: str = "",
    api_key: str = "",
    model: str = "",
) -> dict:
    """Gera questões. Devolve {"cards": [...normalizados...], "aviso": str | None}.

    Faz exatamente UM pedido. Erros viram RuntimeError com mensagem pronta para a tela.
    """
    material = (material or "").strip()
    if not material:
        raise RuntimeError("Escreva o assunto ou cole o material de estudo.")
    if len(material) > MAX_MATERIAL:
        def milhar(x: int) -> str:
            return f"{x:,}".replace(",", ".")   # 40.000, como se escreve em português

        raise RuntimeError(
            f"O material tem {milhar(len(material))} caracteres; o limite por pedido é "
            f"{milhar(MAX_MATERIAL)}. Divida em partes menores (por capítulo ou por tema) "
            "e gere uma de cada vez.")

    client = _client(api_key)
    n = max(1, min(int(n), MAX_QUESTIONS))
    modelo = model or DEFAULT_MODEL

    try:
        interaction = client.interactions.create(
            model=modelo,
            system_instruction=SYSTEM,
            input=_build_prompt(material, n, kind, difficulty, notes),
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": RESPONSE_SCHEMA,
            },
            generation_config=_config_geracao(modelo, n),
        )
    except Exception as exc:
        raise RuntimeError(_mensagem_de_erro(exc, modelo)) from exc

    texto = _texto_da_resposta(interaction)
    status = getattr(interaction, "status", "") or ""
    aviso = None

    validos: list[GeneratedCard] = []
    completa = status in ("completed", "") and bool(texto)
    if completa:
        try:
            validos = GeneratedSet.model_validate_json(texto).cards
        except ValidationError:
            completa = False  # JSON ilegível: tenta aproveitar o que der abaixo

    if not completa:
        # cortada, falhou ou JSON quebrado: aproveita as questões inteiras que houver
        validos = _validar(_recuperar_parcial(texto)) if texto else []
        if not validos:
            raise RuntimeError(_mensagem_de_status(interaction, texto))
        aviso = (f"A resposta veio cortada; aproveitei as {len(validos)} questões completas. "
                 "Para mais, gere de novo pedindo menos questões por vez.")

    cards = [c for c in (_to_card(g) for g in validos) if c]
    if not cards:
        raise RuntimeError("O Gemini devolveu questões, mas todas com defeito (alternativa correta "
                           "inválida ou verso vazio). Tente gerar de novo.")
    descartadas = len(validos) - len(cards)
    if descartadas and not aviso:
        aviso = f"{descartadas} questão(ões) foram descartadas por defeito no formato."
    return {"cards": cards, "aviso": aviso}


def _mensagem_de_erro(exc: Exception, model: str) -> str:
    """Traduz o erro do SDK para algo acionável na tela.

    O SDK levanta classes de módulos internos diferentes conforme o endpoint, então
    olhamos o código HTTP e o texto em vez de amarrar em tipos privados.
    """
    texto = str(exc)
    minusculo = texto.lower()
    codigo = getattr(exc, "status_code", None) or getattr(exc, "code", None)

    if "timeout" in minusculo or "timed out" in minusculo or type(exc).__name__.endswith("Timeout"):
        return (f"O Gemini demorou mais de {int(TIMEOUT_S)} s para responder e o pedido foi "
                "cancelado. Nada foi repetido. Tente de novo ou peça menos questões.")
    if "API_KEY_INVALID" in texto or "API key not valid" in texto or codigo in (401, 403):
        return "Chave da API recusada. Confira a chave salva na aba Gerar com IA."
    if codigo == 404 or "not found" in minusculo:
        return f"O modelo '{model}' não está disponível nesta conta. Escolha outro na lista."
    if codigo == 429 or "RESOURCE_EXHAUSTED" in texto:
        return ("Limite de uso do Gemini atingido (cota ou pedidos por minuto). "
                "Espere um pouco e tente de novo — o programa não repete sozinho.")
    if codigo and int(codigo) >= 500:
        return ("O servidor do Gemini está instável ou sobrecarregado. "
                "O programa não repete sozinho: tente de novo em instantes.")
    if "SAFETY" in texto or "blocked" in minusculo:
        return "O conteúdo foi bloqueado pelos filtros do Gemini. Tente reescrever o material."
    if any(x in minusculo for x in ("connect", "network", "dns", "getaddrinfo", "unreachable")):
        return "Sem conexão com a internet (ou o Google está inacessível). Confira a rede."

    # Resto: mostra o essencial, sem despejar o JSON inteiro.
    resumo = texto.split("[{")[0].strip() or texto[:200]
    from .cofre import ocultar_chaves
    return ocultar_chaves(f"Não consegui falar com a API do Gemini: {resumo}")


def _to_card(g: GeneratedCard) -> dict | None:
    options = [o.strip() for o in (g.options or []) if o and o.strip()]
    if g.kind == "quiz":
        if len(options) < 2 or not (0 <= g.answer_index < len(options)):
            return None
        answer = str(g.answer_index)
    else:
        if not g.answer_text.strip():
            return None
        answer, options = g.answer_text.strip(), []

    return {
        "kind": g.kind,
        "question": g.question.strip(),
        "options": options,
        "answer": answer,
        "explanation": g.explanation.strip(),
        "tags": g.tags.strip(),
    }
