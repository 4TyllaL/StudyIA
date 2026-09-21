"""StudyIA — programa de janela para Windows.

A interface é carregada direto do disco e conversa com o Python por uma ponte interna
(`app/bridge.py`): não existe servidor, porta nem localhost em lugar nenhum.

Como o programa roda sem console, todo erro vira uma caixa de mensagem — senão ele
morreria em silêncio e o usuário ficaria sem pista nenhuma.
"""
from __future__ import annotations

import ctypes
import sys
import traceback
import webbrowser

from app.paths import WEB_DIR

TITULO = "StudyIA"
WINDOWS = sys.platform == "win32"

# Chave de registro do runtime do WebView2 (a "casca" que desenha a janela).
GUID_WEBVIEW2 = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
LINK_WEBVIEW2 = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"

_mutex = None  # precisa sobreviver enquanto o programa roda


# --------------------------------------------------------------------- avisos

def aviso(texto: str, titulo: str = TITULO, erro: bool = False) -> None:
    if WINDOWS:
        ctypes.windll.user32.MessageBoxW(None, texto, titulo, 0x10 if erro else 0x40)
    else:
        print(f"{titulo}: {texto}", file=sys.stderr)


def fechar_splash() -> None:
    """Tela de abertura do PyInstaller — só existe no .exe empacotado."""
    try:
        import pyi_splash  # type: ignore[import-not-found]

        pyi_splash.close()
    except Exception:
        pass


# ------------------------------------------------------- uma janela por vez

def ja_esta_aberto() -> bool:
    """Evita que dois cliques no ícone abram dois StudyIA."""
    global _mutex
    if not WINDOWS:
        return False
    kernel32 = ctypes.windll.kernel32
    _mutex = kernel32.CreateMutexW(None, False, "StudyIA_instancia_unica")
    return kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


def trazer_para_frente() -> None:
    if not WINDOWS:
        return
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, TITULO)
    if hwnd:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)


# ------------------------------------------------------------ pré-requisitos

def webview2_instalado() -> bool:
    if not WINDOWS:
        return True
    import winreg

    locais = [
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{GUID_WEBVIEW2}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{GUID_WEBVIEW2}"),
        (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{GUID_WEBVIEW2}"),
    ]
    for raiz, caminho in locais:
        try:
            with winreg.OpenKey(raiz, caminho) as chave:
                versao, _ = winreg.QueryValueEx(chave, "pv")
                if versao and versao != "0.0.0.0":
                    return True
        except OSError:
            continue
    return False


# ---------------------------------------------------------------------- main

def abrir_janela() -> int:
    import webview

    from app.bridge import Api

    pagina = WEB_DIR / "index.html"
    if not pagina.exists():
        fechar_splash()
        aviso(f"Não encontrei os arquivos da interface em:\n{WEB_DIR}", erro=True)
        return 1

    # `file://` e proposital: se o caminho for passado "cru", o pywebview sobe por conta
    # propria um servidor HTTP local para servi-lo. Com file:// ele nao abre porta nenhuma.
    webview.create_window(
        TITULO,
        pagina.as_uri(),
        js_api=Api(),
        width=1180,
        height=860,
        min_size=(420, 600),
        confirm_close=False,
    )
    fechar_splash()
    webview.start()  # bloqueia até a janela fechar
    return 0


def main(argv: list[str]) -> int:
    if "--desinstalar" in argv or "--remover" in argv:
        from app.uninstall import executar

        return executar(argv)

    if ja_esta_aberto():
        trazer_para_frente()
        fechar_splash()
        return 0

    if not webview2_instalado():
        fechar_splash()
        aviso(
            "O StudyIA precisa do WebView2, um componente gratuito da Microsoft que "
            "normalmente já vem no Windows.\n\n"
            "Vou abrir a página de download: instale o "
            "\"Evergreen Bootstrapper\" e depois abra o StudyIA de novo."
        )
        webbrowser.open(LINK_WEBVIEW2)
        return 1

    return abrir_janela()


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception:
        fechar_splash()
        aviso("O StudyIA encontrou um erro inesperado:\n\n"
              + traceback.format_exc(limit=3), erro=True)
        raise SystemExit(1)
