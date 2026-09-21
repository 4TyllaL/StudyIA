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


class Estado:
    modo = "ok"          # ok | 503 | 429 | travado | incompleto | falhou | lixo
    pedidos = 0
    tamanhos = []        # bytes de cada pedido recebido
    corpos = []


def resposta(modo):
    base = {"id": "x", "created": "2026-01-01T00:00:00Z", "updated": "2026-01-01T00:00:00Z"}
    if modo == "ok":
        return 200, {**base, "status": "completed", "steps": [
            {"type": "model_output", "content": [{"type": "text", "text": json.dumps(CARDS)}]}]}
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
        if m == "travado":
            time.sleep(40)   # servidor que aceita e nunca responde
            return
        status, corpo_resp = resposta(m)
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
