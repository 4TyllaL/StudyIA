"""Proteção de segredos (a chave da API) com a criptografia do próprio Windows — DPAPI.

O dado é cifrado com uma chave derivada da conta do usuário do Windows. Quem copiar o
arquivo `studyia.db` (backup, pendrive, pasta sincronizada na nuvem, outro computador)
recebe só um bloco ilegível: sem estar logado como o mesmo usuário na mesma máquina, não
dá para decifrar. Não precisa de senha e não depende de nenhuma biblioteca extra.

Limite honesto: é proteção *em repouso*. Qualquer programa rodando como o MESMO usuário
consegue pedir ao Windows para decifrar (é assim que o Chrome protege senhas salvas). Por
isso usamos também uma "entropia" própria do StudyIA, para que um programa qualquer não
decifre o valor por acaso, e nunca devolvemos a chave para a interface.
"""
from __future__ import annotations

import base64
import ctypes
import re
import sys
from ctypes import wintypes

PREFIXO = "dpapi1:"
_ENTROPIA = b"StudyIA/chave-api/v1"
_UI_FORBIDDEN = 0x1  # nunca abrir janela pedindo algo ao usuário

# Chaves do Google começam com "AIza"; usado só para não vazar em mensagens de erro.
_PADRAO_CHAVE = re.compile(r"AIza[0-9A-Za-z_\-]{16,}")


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(dados: bytes) -> tuple[_Blob, ctypes.Array]:
    buf = ctypes.create_string_buffer(dados, len(dados))
    return _Blob(len(dados), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _configurar_dll():
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    ponteiro = ctypes.POINTER(_Blob)
    crypt32.CryptProtectData.argtypes = [ponteiro, wintypes.LPCWSTR, ponteiro, wintypes.LPVOID,
                                         wintypes.LPVOID, wintypes.DWORD, ponteiro]
    crypt32.CryptUnprotectData.argtypes = [ponteiro, ctypes.POINTER(wintypes.LPWSTR), ponteiro,
                                           wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, ponteiro]
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    return crypt32, kernel32


def _proteger_bytes(dados: bytes) -> bytes:
    crypt32, kernel32 = _configurar_dll()
    entrada, _b1 = _blob(dados)
    entropia, _b2 = _blob(_ENTROPIA)
    saida = _Blob()
    if not crypt32.CryptProtectData(ctypes.byref(entrada), "StudyIA", ctypes.byref(entropia),
                                    None, None, _UI_FORBIDDEN, ctypes.byref(saida)):
        raise OSError("O Windows não conseguiu proteger o dado.")
    try:
        return ctypes.string_at(saida.pbData, saida.cbData)
    finally:
        kernel32.LocalFree(saida.pbData)


def _desproteger_bytes(dados: bytes) -> bytes:
    crypt32, kernel32 = _configurar_dll()
    entrada, _b1 = _blob(dados)
    entropia, _b2 = _blob(_ENTROPIA)
    saida = _Blob()
    if not crypt32.CryptUnprotectData(ctypes.byref(entrada), None, ctypes.byref(entropia),
                                      None, None, _UI_FORBIDDEN, ctypes.byref(saida)):
        raise OSError("Não foi possível decifrar (outro usuário ou outro computador).")
    try:
        return ctypes.string_at(saida.pbData, saida.cbData)
    finally:
        kernel32.LocalFree(saida.pbData)


def proteger(texto: str) -> str:
    """Devolve o texto cifrado, pronto para gravar no banco. Vazio continua vazio."""
    if not texto:
        return ""
    if sys.platform != "win32":  # o programa é só para Windows; isto evita quebrar testes fora dele
        raise OSError("Proteção de segredos só está disponível no Windows.")
    cifrado = _proteger_bytes(texto.encode("utf-8"))
    return PREFIXO + base64.b64encode(cifrado).decode("ascii")


def ja_protegido(valor: str) -> bool:
    return bool(valor) and valor.startswith(PREFIXO)


def revelar(valor: str) -> str | None:
    """Decifra um valor gravado.

    Devolve None se não for possível decifrar (banco copiado de outro usuário/computador),
    para o chamador tratar como "chave não configurada" em vez de quebrar.
    Valores antigos, sem prefixo, são texto puro de versões anteriores: devolvidos como
    estão, para o chamador migrar.
    """
    if not valor:
        return ""
    if not ja_protegido(valor):
        return valor
    try:
        return _desproteger_bytes(base64.b64decode(valor[len(PREFIXO):])).decode("utf-8")
    except (OSError, ValueError):
        return None


def ocultar_chaves(texto: str) -> str:
    """Tira qualquer coisa com cara de chave de API de uma mensagem antes de mostrar."""
    return _PADRAO_CHAVE.sub("[chave oculta]", texto or "")
