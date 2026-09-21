"""Caminhos de arquivo — funcionam tanto rodando do código quanto no .exe empacotado.

No modo empacotado (PyInstaller), os arquivos da interface ficam numa pasta temporária
somente-leitura, então o banco precisa morar em outro lugar: %LOCALAPPDATA%\\StudyIA.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)


def resource_dir() -> Path:
    """Onde estão os arquivos que acompanham o programa (web/, etc.)."""
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Onde fica o banco. Sobrescreva com a variável STUDYIA_DATA."""
    override = os.environ.get("STUDYIA_DATA")
    if override:
        return Path(override)
    if FROZEN:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "StudyIA"
    return resource_dir() / "data"


WEB_DIR = resource_dir() / "web"
