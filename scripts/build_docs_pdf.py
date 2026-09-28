"""Build the submission PDF from DOCUMENTATION.md (a small Markdown subset, Cyrillic via DejaVu).

  PROTOTYPE_ACCESS="https://… — логин …, пароль …" \\
      uv run --no-sync --with reportlab python scripts/build_docs_pdf.py

The access line replaces {{PROTOTYPE_ACCESS}} only in the PDF, so credentials never enter the repository;
the PDF itself is git-ignored.
"""
import html
import os
import re
import sys

import matplotlib
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, ListFlowable, ListItem, Paragraph, Preformatted,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

FONTS = os.path.join(os.path.dirname(matplotlib.__file__), "mpl-data/fonts/ttf")
for name, file in [("Sans", "DejaVuSans.ttf"), ("Sans-Bold", "DejaVuSans-Bold.ttf"),
                   ("Sans-Italic", "DejaVuSans-Oblique.ttf"), ("Sans-BoldItalic", "DejaVuSans-BoldOblique.ttf"),
                   ("Mono", "DejaVuSansMono.ttf")]:
    pdfmetrics.registerFont(TTFont(name, os.path.join(FONTS, file)))
pdfmetrics.registerFontFamily("Sans", normal="Sans", bold="Sans-Bold", italic="Sans-Italic", boldItalic="Sans-BoldItalic")

INK, MUTED, ACCENT, RULE, CODEBG = (colors.HexColor(c) for c in ("#1d2330", "#5b6475", "#2f5aa8", "#d5dae3", "#f3f5f8"))
base = dict(fontName="Sans", textColor=INK, alignment=TA_LEFT)
S = {
    "title": ParagraphStyle("title", **{**base, "fontName": "Sans-Bold"}, fontSize=20, leading=25, spaceAfter=8),
    "h2": ParagraphStyle("h2", **{**base, "fontName": "Sans-Bold", "textColor": ACCENT}, fontSize=14, leading=18,
                         spaceBefore=12, spaceAfter=6, keepWithNext=1),
    "h3": ParagraphStyle("h3", **{**base, "fontName": "Sans-Bold"}, fontSize=11.5, leading=15, spaceBefore=8, spaceAfter=4, keepWithNext=1),
    "body": ParagraphStyle("body", **base, fontSize=9.8, leading=14, spaceAfter=5),
    "cell": ParagraphStyle("cell", **base, fontSize=8.6, leading=11.5),
    "cellh": ParagraphStyle("cellh", **{**base, "fontName": "Sans-Bold"}, fontSize=8.6, leading=11.5),
    "code": ParagraphStyle("code", fontName="Mono", fontSize=8.2, leading=10.8, textColor=INK),
}


def inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<link href="\2" color="#2f5aa8">\1</link>', text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`([^`]+)`", r'<font name="Mono" size="8.4" color="#8a3a1c">\1</font>', text)
    return text


def table(rows):
    cells = [[Paragraph(inline(c), S["cellh" if i == 0 else "cell"]) for c in r] for i, r in enumerate(rows)]
    width = A4[0] - 36 * mm
    n = len(rows[0])
    widths = [width * w for w in {2: (0.42, 0.58), 3: (0.28, 0.40, 0.32)}.get(n, [1 / n] * n)]
    t = Table(cells, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8edf6")),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def render(md: str, out: str):
    story, lines, i = [], md.splitlines(), 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            block = []
            i += 1
            while not lines[i].startswith("```"):
                block.append(lines[i])
                i += 1
            pre = Preformatted("\n".join(block), S["code"])
            box = Table([[pre]], colWidths=[A4[0] - 36 * mm])
            box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), CODEBG), ("LEFTPADDING", (0, 0), (-1, -1), 6),
                                     ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
            story += [box, Spacer(1, 5)]
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            story += [table(rows), Spacer(1, 6)]
            continue
        elif re.match(r"^(\d+\.|-) ", line):
            ordered = line[0].isdigit()
            items = []
            while i < len(lines) and re.match(r"^(\d+\.|-) ", lines[i]):
                items.append(ListItem(Paragraph(inline(re.sub(r"^(\d+\.|-) ", "", lines[i])), S["body"]), leftIndent=14))
                i += 1
            story.append(ListFlowable(items, bulletType="1" if ordered else "bullet", start=1 if ordered else "•",
                                      bulletFontName="Sans", bulletFontSize=9, leftIndent=14))
            continue
        elif line.startswith("# "):
            story.append(Paragraph(inline(line[2:]), S["title"]))
        elif line.startswith("## "):
            story.append(Paragraph(inline(line[3:]), S["h2"]))
        elif line.startswith("### "):
            story.append(Paragraph(inline(line[4:]), S["h3"]))
        elif line.strip():
            para = [line]
            while i + 1 < len(lines) and lines[i + 1].strip() and not re.match(r"^(#|\||```|\d+\. |- )", lines[i + 1]):
                i += 1
                para.append(lines[i])
            story.append(Paragraph(inline(" ".join(para)), S["body"]))
        i += 1

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Sans", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 10 * mm, "Almond Eye — сопроводительная документация")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, str(doc.page))
        canvas.restoreState()

    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=16 * mm, title="Almond Eye — сопроводительная документация",
                            author="Команда Almond Eye")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


if __name__ == "__main__":
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    access = os.getenv("PROTOTYPE_ACCESS", "ссылка и доступ передаются вместе с документацией")
    target = os.path.join(root, "Almond_Eye_документация.pdf")
    render(open(os.path.join(root, "DOCUMENTATION.md"), encoding="utf-8").read().replace("{{PROTOTYPE_ACCESS}}", access), target)
    print(target)
