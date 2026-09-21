"""Assistente de instalação do StudyIA.

Vira o `StudyIA-Instalador.exe`: leva o programa dentro dele, copia para a pasta do
usuário (sem pedir senha de administrador), cria os atalhos e registra em "Adicionar
ou remover programas".
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from app import __version__  # noqa: E402
from app.winshell import (  # noqa: E402
    criar_atalho, instalacao_existente, pasta_especial, pasta_instalacao_padrao,
    registrar_programa,
)

FUNDO = "#171c26"
SUPERFICIE = "#1e2532"
LINHA = "#2a3242"
TEXTO = "#e7ecf3"
FRACO = "#8d9bb0"
AZUL = "#6c9cff"
VERDE = "#3ecf8e"

FONTE = "Segoe UI"


def recurso(nome: str) -> Path:
    """Arquivo que viaja dentro do instalador (payload, ícone)."""
    base = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False) else RAIZ
    return base / nome


def caminho_do_programa() -> Path:
    embutido = recurso("payload/StudyIA.exe")
    return embutido if embutido.exists() else RAIZ / "executable" / "StudyIA.exe"


def copiar_com_retentativa(origem: Path, destino: Path) -> None:
    """Se o StudyIA estiver aberto, o Windows não deixa sobrescrever o arquivo."""
    for tentativa in range(3):
        try:
            shutil.copy2(origem, destino)
            return
        except PermissionError:
            if tentativa == 0:
                subprocess.run(["taskkill", "/IM", "StudyIA.exe", "/F"],
                               capture_output=True, creationflags=0x08000000)
            time.sleep(1.2)
    raise PermissionError(
        "O StudyIA parece estar aberto. Feche a janela dele e rode o instalador de novo."
    )


def realizar_instalacao(pasta: Path, atalho_area: bool, progresso=lambda valor, texto: None) -> Path:
    """Copia o programa, cria atalhos e registra no Windows. Devolve o caminho do .exe."""
    origem = caminho_do_programa()
    if not origem.exists():
        raise FileNotFoundError("O instalador está incompleto: não achei o programa dentro dele.")

    progresso(15, "Criando a pasta…")
    pasta.mkdir(parents=True, exist_ok=True)

    progresso(40, "Copiando o programa…")
    destino_exe = pasta / "StudyIA.exe"
    copiar_com_retentativa(origem, destino_exe)

    leia = recurso("packaging/LEIA-ME.txt")
    if leia.exists():
        shutil.copy2(leia, pasta / "LEIA-ME.txt")

    progresso(70, "Criando os atalhos…")
    criar_atalho(pasta_especial("Programs") / "StudyIA.lnk", destino_exe, "Estudar com o StudyIA")
    area = pasta_especial("Desktop") / "StudyIA.lnk"
    if atalho_area:
        criar_atalho(area, destino_exe, "Estudar com o StudyIA")
    else:
        area.unlink(missing_ok=True)

    progresso(90, "Registrando o programa…")
    registrar_programa(pasta, destino_exe, __version__, max(1, destino_exe.stat().st_size // 1024))

    progresso(100, "Pronto!")
    return destino_exe


class Instalador(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Instalar o StudyIA")
        self.configure(bg=FUNDO)
        self.resizable(False, False)
        self._centralizar(560, 420)

        icone = recurso("assets/studyia.ico")
        if icone.exists():
            try:
                self.iconbitmap(str(icone))
            except tk.TclError:
                pass

        self.destino = tk.StringVar(value=str(instalacao_existente() or pasta_instalacao_padrao()))
        self.atalho_area = tk.BooleanVar(value=True)
        self.abrir_no_fim = tk.BooleanVar(value=True)
        self.atualizando = instalacao_existente() is not None

        self.corpo = tk.Frame(self, bg=FUNDO)
        self.corpo.pack(fill="both", expand=True)
        self.tela_inicial()

    # ------------------------------------------------------------- utilidades

    def _centralizar(self, larg: int, alt: int) -> None:
        x = (self.winfo_screenwidth() - larg) // 2
        y = (self.winfo_screenheight() - alt) // 3
        self.geometry(f"{larg}x{alt}+{x}+{y}")

    def _limpar(self) -> None:
        for w in self.corpo.winfo_children():
            w.destroy()

    def _titulo(self, texto: str, sub: str = "") -> None:
        cabecalho = tk.Frame(self.corpo, bg=SUPERFICIE)
        cabecalho.pack(fill="x")
        interno = tk.Frame(cabecalho, bg=SUPERFICIE)
        interno.pack(fill="x", padx=28, pady=22)

        png = recurso("assets/studyia.png")
        if png.exists():
            try:
                self._logo = tk.PhotoImage(file=str(png)).subsample(4, 4)
                tk.Label(interno, image=self._logo, bg=SUPERFICIE).pack(side="left", padx=(0, 16))
            except tk.TclError:
                pass

        coluna = tk.Frame(interno, bg=SUPERFICIE)
        coluna.pack(side="left", anchor="w")
        tk.Label(coluna, text=texto, bg=SUPERFICIE, fg=TEXTO,
                 font=(FONTE, 17, "bold")).pack(anchor="w")
        if sub:
            tk.Label(coluna, text=sub, bg=SUPERFICIE, fg=FRACO,
                     font=(FONTE, 9)).pack(anchor="w", pady=(3, 0))
        credito = tk.Frame(coluna, bg=SUPERFICIE)
        credito.pack(anchor="w", pady=(2, 0))
        tk.Label(credito, text=f"Version {__version__} · Developed by ", bg=SUPERFICIE, fg=FRACO,
                 font=(FONTE, 9)).pack(side="left")
        autor = tk.Label(credito, text="Atylla Azevedo", bg=SUPERFICIE, fg=AZUL,
                         font=(FONTE, 9, "underline"), cursor="hand2")
        autor.pack(side="left")
        autor.bind("<Button-1>", lambda _e: webbrowser.open("https://github.com/4TyllaL/"))

        tk.Frame(self.corpo, bg=LINHA, height=1).pack(fill="x")

    def _botao(self, pai, texto: str, comando, destaque: bool = False) -> tk.Button:
        return tk.Button(
            pai, text=texto, command=comando, font=(FONTE, 10, "bold" if destaque else "normal"),
            bg=AZUL if destaque else SUPERFICIE, fg="#ffffff" if destaque else TEXTO,
            activebackground=AZUL if destaque else LINHA, activeforeground="#ffffff",
            relief="flat", bd=0, padx=20, pady=9, cursor="hand2",
        )

    def _caixa(self, pai, variavel, texto: str) -> tk.Checkbutton:
        return tk.Checkbutton(
            pai, text=texto, variable=variavel, bg=FUNDO, fg=TEXTO, font=(FONTE, 10),
            activebackground=FUNDO, activeforeground=TEXTO, selectcolor=SUPERFICIE,
            highlightthickness=0, bd=0, anchor="w", cursor="hand2",
        )

    # ------------------------------------------------------------ tela inicial

    def tela_inicial(self) -> None:
        self._limpar()
        self._titulo(
            "Atualizar o StudyIA" if self.atualizando else "Instalar o StudyIA",
            "estudo por repetição espaçada",
        )

        meio = tk.Frame(self.corpo, bg=FUNDO)
        meio.pack(fill="both", expand=True, padx=28, pady=20)

        tk.Label(meio, text="O StudyIA será instalado em:", bg=FUNDO, fg=TEXTO,
                 font=(FONTE, 10)).pack(anchor="w")

        linha = tk.Frame(meio, bg=FUNDO)
        linha.pack(fill="x", pady=(8, 4))
        entrada = tk.Entry(linha, textvariable=self.destino, font=(FONTE, 9),
                           bg=SUPERFICIE, fg=TEXTO, insertbackground=TEXTO,
                           relief="flat", bd=8)
        entrada.pack(side="left", fill="x", expand=True)
        self._botao(linha, "Procurar…", self.escolher_pasta).pack(side="left", padx=(8, 0))

        tk.Label(meio, text="Não precisa de senha de administrador — a instalação é só "
                            "para o seu usuário.", bg=FUNDO, fg=FRACO,
                 font=(FONTE, 8), wraplength=480, justify="left").pack(anchor="w", pady=(0, 18))

        self._caixa(meio, self.atalho_area, "Criar atalho na Área de Trabalho").pack(anchor="w")
        self._caixa(meio, self.abrir_no_fim, "Abrir o StudyIA quando terminar").pack(anchor="w")

        self.rodape(esquerda="Cancelar", direita="Atualizar" if self.atualizando else "Instalar",
                    acao_direita=self.instalar)

    def escolher_pasta(self) -> None:
        escolhida = filedialog.askdirectory(title="Onde instalar o StudyIA?")
        if escolhida:
            caminho = Path(escolhida)
            if caminho.name.lower() != "studyia":
                caminho = caminho / "StudyIA"
            self.destino.set(str(caminho))

    def rodape(self, esquerda: str, direita: str, acao_direita) -> None:
        tk.Frame(self.corpo, bg=LINHA, height=1).pack(fill="x", side="bottom")
        barra = tk.Frame(self.corpo, bg=FUNDO)
        barra.pack(fill="x", side="bottom", padx=28, pady=18)
        self._botao(barra, direita, acao_direita, destaque=True).pack(side="right")
        if esquerda:
            self._botao(barra, esquerda, self.destroy).pack(side="right", padx=(0, 8))

    # -------------------------------------------------------------- instalação

    def tela_progresso(self) -> None:
        self._limpar()
        self._titulo("Instalando…", "leva só alguns segundos")

        meio = tk.Frame(self.corpo, bg=FUNDO)
        meio.pack(fill="both", expand=True, padx=28, pady=40)

        estilo = ttk.Style(self)
        estilo.theme_use("default")
        estilo.configure("StudyIA.Horizontal.TProgressbar", troughcolor=SUPERFICIE,
                         background=AZUL, bordercolor=SUPERFICIE, lightcolor=AZUL,
                         darkcolor=AZUL, thickness=8)

        self.barra = ttk.Progressbar(meio, style="StudyIA.Horizontal.TProgressbar",
                                     maximum=100, length=480)
        self.barra.pack(pady=(10, 14))
        self.passo = tk.Label(meio, text="Preparando…", bg=FUNDO, fg=FRACO, font=(FONTE, 10))
        self.passo.pack()

    def instalar(self) -> None:
        self.tela_progresso()
        threading.Thread(target=self._instalar_em_segundo_plano, daemon=True).start()

    def _progresso(self, valor: int, texto: str) -> None:
        def aplicar():
            self.barra["value"] = valor
            self.passo.config(text=texto)
        self.after(0, aplicar)

    def _instalar_em_segundo_plano(self) -> None:
        try:
            pasta = Path(self.destino.get())
            exe = realizar_instalacao(pasta, self.atalho_area.get(), self._progresso)
            time.sleep(0.4)
            self.after(0, lambda: self.tela_final(pasta, exe))
        except Exception as exc:
            self.after(0, lambda: self.tela_erro(exc))

    # ------------------------------------------------------------ telas finais

    def tela_final(self, pasta: Path, exe: Path) -> None:
        self._limpar()
        self._titulo("StudyIA instalado", "bons estudos!")

        meio = tk.Frame(self.corpo, bg=FUNDO)
        meio.pack(fill="both", expand=True, padx=28, pady=24)
        tk.Label(meio, text="✓", bg=FUNDO, fg=VERDE, font=(FONTE, 34, "bold")).pack(anchor="w")
        tk.Label(meio, text=f"Instalado em:\n{pasta}", bg=FUNDO, fg=TEXTO, justify="left",
                 font=(FONTE, 10), wraplength=480).pack(anchor="w", pady=(6, 12))
        tk.Label(meio, text="Você encontra o StudyIA no Menu Iniciar. Para remover, use "
                            "'Adicionar ou remover programas' do Windows.",
                 bg=FUNDO, fg=FRACO, font=(FONTE, 9), wraplength=480,
                 justify="left").pack(anchor="w")

        def concluir():
            if self.abrir_no_fim.get():
                subprocess.Popen([str(exe)], cwd=str(pasta), close_fds=True)
            self.destroy()

        self.rodape(esquerda="", direita="Concluir", acao_direita=concluir)

    def tela_erro(self, exc: Exception) -> None:
        self._limpar()
        self._titulo("Não deu certo", "nada foi alterado no seu computador")

        meio = tk.Frame(self.corpo, bg=FUNDO)
        meio.pack(fill="both", expand=True, padx=28, pady=24)
        tk.Label(meio, text=str(exc), bg=FUNDO, fg=TEXTO, font=(FONTE, 10),
                 wraplength=480, justify="left").pack(anchor="w")

        self.rodape(esquerda="Fechar", direita="Tentar de novo", acao_direita=self.tela_inicial)


def instalar_silencioso(argv: list[str]) -> int:
    """Sem janela: para instalar em vários computadores ou testar.

        StudyIA-Instalador.exe --silencioso [--destino <pasta>] [--sem-atalho-area]
    """
    pasta = instalacao_existente() or pasta_instalacao_padrao()
    if "--destino" in argv and argv.index("--destino") + 1 < len(argv):
        pasta = Path(argv[argv.index("--destino") + 1])
    try:
        exe = realizar_instalacao(pasta, "--sem-atalho-area" not in argv)
    except Exception as exc:
        print(f"Falhou: {exc}", file=sys.stderr)
        return 1
    print(f"Instalado em {exe}")
    return 0


def main(argv: list[str]) -> int:
    if sys.platform != "win32":
        print("O instalador do StudyIA é para Windows.", file=sys.stderr)
        return 1
    if "--silencioso" in argv:
        return instalar_silencioso(argv)
    Instalador().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
