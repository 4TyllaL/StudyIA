"""Desinstalação — roda como `StudyIA.exe --desinstalar`.

Um programa não consegue apagar a pasta onde ele mesmo está rodando. Então o fluxo é:
o executável instalado se copia para a pasta temporária, chama a cópia com `--remover`
e sai; a cópia espera ele morrer, apaga tudo e some junto no próximo boot.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .paths import data_dir
from .winshell import apagar_registro, atalhos_conhecidos, instalacao_existente

SIM_NAO = 0x04 | 0x20      # MB_YESNO | MB_ICONQUESTION
OK = 0x40                  # MB_ICONINFORMATION
ERRO = 0x10
RESPOSTA_SIM = 6


def _perguntar(texto: str) -> bool:
    return ctypes.windll.user32.MessageBoxW(None, texto, "Desinstalar StudyIA", SIM_NAO) == RESPOSTA_SIM


def _avisar(texto: str, icone: int = OK) -> None:
    ctypes.windll.user32.MessageBoxW(None, texto, "StudyIA", icone)


# Só isto é removido. Nunca a pasta inteira: o usuário pode ter escolhido, na instalação,
# uma pasta que já tinha arquivos dele (Documentos, Área de Trabalho...), e apagá-la
# junto seria perda de dados.
ARQUIVOS_DO_PROGRAMA = ("StudyIA.exe", "LEIA-ME.txt")
ARQUIVOS_DE_DADOS = ("studyia.db", "studyia.db-wal", "studyia.db-shm")


def _apagar_arquivo(arquivo: Path, tentativas: int = 40) -> bool:
    """O Windows segura o arquivo por alguns instantes depois que o processo sai."""
    for _ in range(tentativas):
        try:
            arquivo.unlink(missing_ok=True)
            return True
        except OSError:
            time.sleep(0.25)
    return not arquivo.exists()


def _apagar_lista(pasta: Path, nomes: tuple[str, ...]) -> bool:
    """Apaga só os arquivos nomeados e depois a pasta — mas só se ficar vazia."""
    ok = all(_apagar_arquivo(pasta / nome) for nome in nomes)
    try:
        pasta.rmdir()  # falha (e é ignorado) se sobrou algo que não é nosso
    except OSError:
        pass
    return ok


def _iniciar_remocao(pasta: Path, com_dados: bool, silencioso: bool) -> int:
    copia = Path(os.environ.get("TEMP", ".")) / "studyia-desinstalador.exe"
    try:
        shutil.copy2(sys.executable, copia)
    except OSError as exc:
        _avisar(f"Não consegui preparar a desinstalação:\n{exc}", ERRO)
        return 1

    argumentos = [str(copia), "--remover", str(pasta)]
    if com_dados:
        argumentos.append("--com-dados")
    if silencioso:
        argumentos.append("--silencioso")
    subprocess.Popen(argumentos, close_fds=True)
    return 0


def _remover(pasta: Path, com_dados: bool, silencioso: bool) -> int:
    time.sleep(1.0)  # dá tempo do StudyIA original encerrar

    for atalho in atalhos_conhecidos():
        try:
            atalho.unlink(missing_ok=True)
        except OSError:
            pass

    apagar_registro()
    ok = _apagar_lista(pasta, ARQUIVOS_DO_PROGRAMA)

    if com_dados:
        _apagar_lista(data_dir(), ARQUIVOS_DE_DADOS)

    if not silencioso:
        if ok:
            extra = "" if com_dados else "\n\nSuas questões e seu progresso foram mantidos."
            _avisar("O StudyIA foi removido do computador." + extra)
        else:
            _avisar(f"Removi os atalhos, mas o programa ainda parece estar em uso:\n{pasta}\n\n"
                    "Feche o StudyIA e apague só o arquivo StudyIA.exe dessa pasta.", ERRO)

    _apagar_a_si_mesmo()
    return 0 if ok else 1


def _apagar_a_si_mesmo() -> None:
    """A cópia temporária não consegue se apagar enquanto roda; um cmd separado espera
    ela sair e faz isso por ela."""
    if not getattr(sys, "frozen", False):
        return
    copia = Path(sys.executable)
    if copia.name != "studyia-desinstalador.exe":  # nunca apagar outro executável por engano
        return
    # String única, não lista: com lista o subprocess escapa as aspas internas com \" e o
    # cmd não entende — o `del` nunca rodava (testado com e sem espaço no caminho).
    subprocess.Popen(
        f'cmd /c "ping -n 4 127.0.0.1 >nul & del /f /q "{copia}""',
        creationflags=0x08000000,  # CREATE_NO_WINDOW
        close_fds=True,
    )


def executar(argv: list[str]) -> int:
    silencioso = "--silencioso" in argv

    if "--remover" in argv:  # segunda etapa: já estamos rodando da cópia temporária
        indice = argv.index("--remover")
        pasta = Path(argv[indice + 1]) if len(argv) > indice + 1 else Path(sys.executable).parent
        return _remover(pasta, "--com-dados" in argv, silencioso)

    pasta = instalacao_existente() or Path(sys.executable).parent
    com_dados = "--com-dados" in argv  # no modo silencioso não há a quem perguntar

    if not silencioso:
        if not _perguntar("Deseja remover o StudyIA deste computador?"):
            return 0
        com_dados = _perguntar(
            "Apagar também suas questões, baralhos e todo o progresso?\n\n"
            "Escolha Não para manter tudo — se você reinstalar o StudyIA depois, "
            "seus estudos continuam de onde pararam."
        )

    return _iniciar_remocao(pasta, com_dados, silencioso)
