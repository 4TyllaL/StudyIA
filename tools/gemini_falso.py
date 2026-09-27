"""Servidor Gemini falso (só para testes): conta os pedidos e simula falhas.

Serve para provar comportamentos que não dá para testar com a API de verdade — servidor
sobrecarregado, travado, resposta cortada — e para contar quantos pedidos o programa faz.
Aponte o SDK para ele com a variável GOOGLE_GEMINI_BASE_URL.
"""
import json, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CARDS = {"cards": [
    {"kind": "quiz", "question": f"Pergunta {i}?", "options": ["a", "b", "c", "d"], "answer_index": i % 4,
     "answer_text": "", "explanation": "porque sim", "tags": "t"} for i in range(5)]}

# Respostas do modo debate: o servidor escolhe pelo schema que veio no pedido.
TEMAS = {"resumo": "Material sobre o regime disciplinar do servidor.", "temas": [
    {"titulo": f"Tema {i}", "resumo": f"Resumo do tema {i}.", "tese": f"Tese discutível {i}.",
     "pontos": [f"Ponto {i}.{j}" for j in range(3)]} for i in range(1, 4)]}
TURNO = {"fala": "Será mesmo? Então me explique por que o prazo é de 30 dias.", "lacuna": ""}
TURNO_COM_LACUNA = {"fala": "Não é bem assim: o prazo é de 30 dias. Por quê?",
                    "lacuna": "Confundiu o prazo de recurso com o de defesa."}
RESUMO = {"dominou": ["Entende a finalidade do recurso."],
          "revisar": ["Prazo de recurso (30 dias) versus prazo de defesa."],
          "cards": CARDS["cards"][:3]}


class Estado:
    modo = "ok"          # ok | 503 | 429 | travado | incompleto | falhou | lixo
    pedidos = 0
    tamanhos = []        # bytes de cada pedido recebido
    corpos = []


def _conteudo_ok(corpo):
    """Escolhe a resposta pelo schema pedido (questões, temas, turno do debate ou resumo)."""
    try:
        pedido = json.loads(corpo)
    except ValueError:
        return CARDS
    props = (((pedido.get("response_format") or {}).get("schema") or {}).get("properties") or {})
    if "temas" in props:
        return TEMAS
    if "fala" in props:
        entrada = json.dumps(pedido.get("input", ""), ensure_ascii=False)
        return TURNO_COM_LACUNA if "10 dias" in entrada.rsplit("Estudante:", 1)[-1] else TURNO
    if "dominou" in props:
        return RESUMO
    return CARDS


def resposta(modo, corpo=b"{}"):
    base = {"id": "x", "created": "2026-01-01T00:00:00Z", "updated": "2026-01-01T00:00:00Z"}
    if modo == "ok":
        return 200, {**base, "status": "completed", "steps": [
            {"type": "model_output", "content": [{"type": "text", "text": json.dumps(_conteudo_ok(corpo))}]}]}
    if modo == "incompleto":
        return 200, {**base, "status": "incomplete", "steps": [
            {"type": "thought", "summary": [{"type": "text", "text": "pensando..."}]}]}
    if modo == "falhou":
        return 200, {**base, "status": "failed", "steps": [], "errors": [{"message": "modelo indisponivel"}]}
    if modo == "lixo":
        return 200, {**base, "status": "completed", "steps": [
            {"type": "model_output", "content": [{"type": "text", "text": "Claro! Aqui estao suas questoes: ..."}]}]}
    if modo == "cortado":
        return 200, {**base, "status": "completed", "steps": [
            {"type": "model_output", "content": [{"type": "text", "text": json.dumps(CARDS)[:200]}]}]}
    if modo == "invalidas":  # JSON valido, mas todas as questoes sao inutilizaveis
        ruins = {"cards": [{"kind": "quiz", "question": "Q?", "options": ["a", "b"], "answer_index": 9,
                            "answer_text": "", "explanation": "", "tags": ""}]}
        return 200, {**base, "status": "completed", "steps": [
            {"type": "model_output", "content": [{"type": "text", "text": json.dumps(ruins)}]}]}
    return 200, {}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        corpo = self.rfile.read(n)
        Estado.pedidos += 1
        Estado.tamanhos.append(len(corpo))
        Estado.corpos.append(corpo)
        m = Estado.modo
        if m in ("503", "429"):
            self.send_response(int(m)); self.send_header("Content-Type", "application/json")
            self.end_headers(); self.wfile.write(json.dumps({"error": {"code": int(m), "message": "overloaded", "status": "UNAVAILABLE"}}).encode())
            return
        if m == "recusa-nivel" and b"thinking_level" in corpo:
            erro = {"error": {"message": "'low' is not a supported thinking level for this model. "
                                         "Allowed values are: high, medium.", "code": "invalid_request"}}
            dados = json.dumps(erro).encode()
            self.send_response(400); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(dados))); self.end_headers(); self.wfile.write(dados)
            return
        if m == "recusa-nivel":
            m = "ok"
        if m == "travado":
            time.sleep(40)   # servidor que aceita e nunca responde
            return
        status, corpo_resp = resposta(m, corpo)
        dados = json.dumps(corpo_resp).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados))); self.end_headers(); self.wfile.write(dados)

    def do_GET(self):
        dados = json.dumps({"models": [{"name": "models/gemini-3.8-flash"}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados))); self.end_headers(); self.wfile.write(dados)


def iniciar():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]
