"""Coisas de Windows compartilhadas pelo instalador e pelo desinstalador:
atalhos (.lnk), pastas do sistema e o registro de "Adicionar ou remover programas".

Tudo por usuário (HKCU e %LOCALAPPDATA%) — assim a instalação nunca pede senha de
administrador.
"""
from __future__ import annotations

import os
import subprocess
import sys
import winreg
from pathlib import Path

CHAVE_DESINSTALAR = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\StudyIA"
SEM_JANELA = 0x08000000  # CREATE_NO_WINDOW: o PowerShell não pisca na tela


def pasta_especial(nome: str, padrao: str = "") -> Path:
    """Caminho real de uma pasta do Windows ('Desktop', 'Programs', 'Local AppData').

    Lemos do registro porque o usuário pode ter a Área de Trabalho no OneDrive ou em
    outro disco — cravar %USERPROFILE%\\Desktop criaria atalho no lugar errado.
    """
    try:
        chave = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, chave) as k:
            valor, _ = winreg.QueryValueEx(k, nome)
            if valor:
                return Path(valor)
    except OSError:
        pass
    return Path(padrao or os.path.expanduser("~"))


def pasta_instalacao_padrao() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(base) / "Programs" / "StudyIA"


def criar_atalho(atalho: Path, alvo: Path, descricao: str = "", argumentos: str = "") -> None:
    def aspas(texto: str) -> str:
        return str(texto).replace("'", "''")

    script = (
        "$w = New-Object -ComObject WScript.Shell; "
        f"$s = $w.CreateShortcut('{aspas(atalho)}'); "
        f"$s.TargetPath = '{aspas(alvo)}'; "
        f"$s.WorkingDirectory = '{aspas(alvo.parent)}'; "
        f"$s.IconLocation = '{aspas(alvo)},0'; "
        f"$s.Description = '{aspas(descricao)}'; "
        + (f"$s.Arguments = '{aspas(argumentos)}'; " if argumentos else "")
        + "$s.Save()"
    )
    atalho.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        creationflags=SEM_JANELA, check=True, capture_output=True,
    )


def registrar_programa(pasta: Path, exe: Path, versao: str, tamanho_kb: int) -> None:
    """Faz o StudyIA aparecer em 'Adicionar ou remover programas'."""
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, CHAVE_DESINSTALAR) as k:
        valores = {
            "DisplayName": "StudyIA",
            "DisplayVersion": versao,
            "Publisher": "StudyIA",
            "DisplayIcon": str(exe),
            "InstallLocation": str(pasta),
            "UninstallString": f'"{exe}" --desinstalar',
            "QuietUninstallString": f'"{exe}" --desinstalar --silencioso',
        }
        for nome, valor in valores.items():
            winreg.SetValueEx(k, nome, 0, winreg.REG_SZ, valor)
        for nome, valor in (("NoModify", 1), ("NoRepair", 1), ("EstimatedSize", tamanho_kb)):
            winreg.SetValueEx(k, nome, 0, winreg.REG_DWORD, valor)


def apagar_registro() -> None:
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, CHAVE_DESINSTALAR)
    except OSError:
        pass


def instalacao_existente() -> Path | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CHAVE_DESINSTALAR) as k:
            valor, _ = winreg.QueryValueEx(k, "InstallLocation")
            caminho = Path(valor)
            return caminho if caminho.exists() else None
    except OSError:
        return None


def atalhos_conhecidos() -> list[Path]:
    return [
        pasta_especial("Programs") / "StudyIA.lnk",
        pasta_especial("Desktop") / "StudyIA.lnk",
    ]


def esta_instalado_aqui() -> bool:
    """O executável em execução está dentro da pasta instalada?"""
    atual = Path(sys.executable).resolve()
    pasta = instalacao_existente()
    return bool(pasta and pasta.resolve() in atual.parents)
