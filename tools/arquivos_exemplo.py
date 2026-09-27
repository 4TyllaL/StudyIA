"""Monta um PDF e um .docx pequenos, com texto de verdade, para os testes do modo debate.

Sem dependência extra: o PDF é escrito à mão (uma página, fonte Helvetica) e o .docx é
o zip mínimo que o Word aceita.
"""
from __future__ import annotations

import io
import zipfile

TEXTO = [
    "Lei 8.112/90 - regime disciplinar.",
    "O servidor tem 30 dias para recorrer de penalidade disciplinar.",
    "O prazo de defesa no processo administrativo disciplinar e de 10 dias.",
    "A demissao e aplicada nos casos de abandono de cargo e improbidade.",
]


def pdf(linhas: list[str] = TEXTO) -> bytes:
    texto = "BT /F1 12 Tf 50 780 Td 16 TL " + " ".join(
        "(" + ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") '" for ln in linhas) + " ET"
    objetos = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(texto)} >>\nstream\n{texto}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    saida = io.BytesIO()
    saida.write(b"%PDF-1.4\n")
    posicoes = []
    for i, obj in enumerate(objetos, 1):
        posicoes.append(saida.tell())
        saida.write(f"{i} 0 obj\n{obj}\nendobj\n".encode("latin-1"))
    xref = saida.tell()
    saida.write(f"xref\n0 {len(objetos) + 1}\n0000000000 65535 f \n".encode())
    for p in posicoes:
        saida.write(f"{p:010d} 00000 n \n".encode())
    saida.write(f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return saida.getvalue()


def docx(linhas: list[str] = TEXTO) -> bytes:
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    corpo = "".join(f"<w:p><w:r><w:t>{ln}</w:t></w:r></w:p>" for ln in linhas)
    arquivos = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'),
        "word/document.xml": f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{w}"><w:body>{corpo}</w:body></w:document>',
    }
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as z:
        for nome, conteudo in arquivos.items():
            z.writestr(nome, conteudo)
    return saida.getvalue()
