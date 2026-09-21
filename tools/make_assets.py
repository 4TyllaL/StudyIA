"""Gera o ícone e a tela de abertura usados no empacotamento.

Rode só quando quiser mudar a identidade visual:
    .venv\\Scripts\\python.exe tools\\make_assets.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RAIZ = Path(__file__).resolve().parent.parent
ASSETS = RAIZ / "assets"

FUNDO = (23, 28, 38)
AZUL = (108, 156, 255)
BRANCO = (231, 236, 243)
VERDE = (62, 207, 142)


def fonte(tamanho: int, negrito: bool = True) -> ImageFont.FreeTypeFont:
    for nome in ("segoeuib.ttf" if negrito else "segoeui.ttf", "arialbd.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(nome, tamanho)
        except OSError:
            continue
    return ImageFont.load_default(tamanho)


def desenhar_icone(tam: int = 512) -> Image.Image:
    """Duas cartas empilhadas com um visto — o resumo visual do app."""
    img = Image.new("RGBA", (tam, tam), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = tam / 512  # tudo em proporção do lado

    d.rounded_rectangle([0, 0, tam, tam], radius=112 * u, fill=FUNDO)

    # carta de trás, levemente girada
    tras = Image.new("RGBA", (tam, tam), (0, 0, 0, 0))
    ImageDraw.Draw(tras).rounded_rectangle(
        [120 * u, 110 * u, 392 * u, 400 * u], radius=28 * u, fill=(46, 58, 82)
    )
    img.alpha_composite(tras.rotate(-9, resample=Image.BICUBIC, center=(tam / 2, tam / 2)))

    # carta da frente
    d.rounded_rectangle([112 * u, 132 * u, 384 * u, 408 * u], radius=28 * u,
                        fill=(30, 37, 50), outline=AZUL, width=int(7 * u))
    # linhas de texto
    for i, largura in enumerate((180, 140, 160)):
        y = 186 * u + i * 40 * u
        d.rounded_rectangle([150 * u, y, (150 + largura) * u, y + 15 * u],
                            radius=8 * u, fill=(96, 110, 136))

    # visto verde
    d.line([(172 * u, 336 * u), (212 * u, 372 * u), (322 * u, 252 * u)],
           fill=VERDE, width=int(34 * u), joint="curve")
    return img


def salvar_icone() -> Path:
    base = desenhar_icone(512)
    destino = ASSETS / "studyia.ico"
    base.save(destino, format="ICO",
              sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    base.resize((256, 256), Image.LANCZOS).save(ASSETS / "studyia.png")
    return destino


def salvar_splash() -> Path:
    """Tela mostrada enquanto o .exe se descompacta (3 a 4 segundos)."""
    larg, alt = 460, 240
    img = Image.new("RGB", (larg, alt), FUNDO)
    d = ImageDraw.Draw(img)

    icone = desenhar_icone(96)
    img.paste(icone, (40, 56), icone)

    d.text((158, 74), "StudyIA", font=fonte(44), fill=BRANCO)
    d.text((160, 130), "abrindo…", font=fonte(19, negrito=False), fill=(141, 155, 176))
    d.rounded_rectangle([160, 168, 420, 174], radius=3, fill=(46, 58, 82))
    d.rounded_rectangle([160, 168, 250, 174], radius=3, fill=AZUL)

    destino = ASSETS / "splash.png"
    img.save(destino)
    return destino


if __name__ == "__main__":
    ASSETS.mkdir(exist_ok=True)
    print("ícone:", salvar_icone())
    print("splash:", salvar_splash())
