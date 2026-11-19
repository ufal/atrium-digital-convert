#!/usr/bin/env python3
"""data_samples/pdf/make_synthetic_pdfs.py — the synthetic born-digital samples (atrium-project#16, #20).

Writes CTX000000004.pdf, CTX000000005.pdf and CTX000000006.pdf beside this file. Everything in them
is invented: the site (Hradiště u Horní Mezí), the people, the finds, the photographs and the
plans. They are not AMČR documents and are no reference for any real site; they share the invented
world of the synthetic ALTO samples CTX000000001-3 used across the ATRIUM repositories.

Each document exercises page verdicts of the converter (`pages[].text_layer`, `needsAtr`):

    CTX000000004  born-digital report, 9 pages: roman page labels (i, ii) on the front matter,
                  bold body-size section titles, a running header and page numbers, two columns,
                  a ruled table, a photograph with a letter-spaced caption, and a plan whose labels
                  are part of the image (its only text is the running header and page number).
    CTX000000005  a scan put into a PDF, 4 pages: two typewritten pages with an invisible OCR text
                  layer (render mode 3), a sketch without text, and a typewritten page whose only
                  text is a page number stamped over the image.
    CTX000000006  4 pages of text layers: a clean page; a page whose font gives no Unicode for
                  ř ě č ů (some words lose a letter); a page whose font maps every glyph to U+FFFD;
                  a page of CP1250 bytes under a WinAnsi font (mojibake).

Not part of any image or test. Needs reportlab, Pillow and pypdf (not in the requirement files) and
the DejaVu and Liberation fonts (Debian: fonts-dejavu-core, fonts-liberation):

    pip install reportlab Pillow pypdf
    python3 data_samples/pdf/make_synthetic_pdfs.py [--out DIR]
"""

from __future__ import annotations

import argparse
import io
import random
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

HERE = Path(__file__).resolve().parent
FONT_DIRS = [Path("/usr/share/fonts/truetype/liberation"), Path("/usr/share/fonts/truetype/dejavu")]
W, H = A4
MARGIN = 70.0
HEADER = "Hradiště u Horní Mezí — zpráva o výzkumu 2024"
SUBJECT = "Synthetic sample: invented content, not an AMČR document (atrium-project#20)"


def font_path(name: str) -> str:
    for d in FONT_DIRS:
        if (d / name).is_file():
            return str(d / name)
    raise SystemExit(f"font {name} not found in {', '.join(map(str, FONT_DIRS))}")


def register_fonts() -> None:
    for alias, file in [
        ("Serif", "LiberationSerif-Regular.ttf"),
        ("Serif-Bold", "LiberationSerif-Bold.ttf"),
        ("Serif-Italic", "LiberationSerif-Italic.ttf"),
        ("Sans", "LiberationSans-Regular.ttf"),
        ("Sans-Bold", "LiberationSans-Bold.ttf"),
        ("DejaVuSerif", "DejaVuSerif.ttf"),
        ("DejaVuSerif-Bold", "DejaVuSerif-Bold.ttf"),
        ("DejaVuSans", "DejaVuSans.ttf"),
    ]:
        pdfmetrics.registerFont(TTFont(alias, font_path(file)))


def new_canvas(buf: io.BytesIO, title: str) -> canvas.Canvas:
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    c.setTitle(title)
    c.setAuthor("Jan Novotný (invented)")
    c.setSubject(SUBJECT)
    c.setCreator("atrium-digital-convert data_samples/pdf/make_synthetic_pdfs.py")
    return c


def wrap(text: str, font: str, size: float, width: float) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if pdfmetrics.stringWidth(trial, font, size) <= width:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def paragraph(c, text, x, y, width, font="Serif", size=11.0, leading=14.5, indent=18.0) -> float:
    """Justified-looking left-aligned paragraph; returns the y below it."""
    lines = wrap(text, font, size, width - indent)
    c.setFont(font, size)
    for i, line in enumerate(lines):
        c.drawString(x + (indent if i == 0 else 0), y, line)
        y -= leading
    return y - 4


def furniture(c, number: str) -> None:
    c.setFont("Sans", 8.5)
    c.drawString(MARGIN, H - 40, HEADER)
    c.setLineWidth(0.4)
    c.line(MARGIN, H - 44, W - MARGIN, H - 44)
    c.setFont("Serif", 10)
    c.drawCentredString(W / 2, 32, number)


def heading(c, text: str, y: float) -> float:
    """A section title set in bold at the body size, as a word processor's bold paragraph."""
    c.setFont("Serif-Bold", 11)
    c.drawString(MARGIN, y, text)
    return y - 20


# ── images (seeded, deterministic) ──────────────────────────────────────────────────────────


def photo_image(seed: int, size=(1200, 860)) -> Image.Image:
    """A trench section: soil layers, a darker pit fill, a scale bar and a north arrow."""
    rng = random.Random(seed)
    w, h = size
    img = Image.new("RGB", size)
    px = img.load()
    bands = [
        (0.0, (150, 160, 120)),
        (0.18, (120, 92, 60)),
        (0.45, (150, 115, 72)),
        (0.75, (175, 150, 105)),
    ]
    for y in range(h):
        base = bands[0][1]
        for start, colour in bands:
            if y / h + 0.03 * rng.random() >= start:
                base = colour
        for x in range(w):
            n = rng.randint(-14, 14)
            px[x, y] = tuple(max(0, min(255, v + n)) for v in base)
    d = ImageDraw.Draw(img)
    d.ellipse((w * 0.35, h * 0.38, w * 0.68, h * 0.92), fill=(78, 60, 44))
    for _ in range(40):
        x, y = rng.uniform(0.36, 0.66) * w, rng.uniform(0.45, 0.88) * h
        r = rng.uniform(3, 9)
        d.ellipse((x - r, y - r, x + r, y + r), fill=(rng.randint(150, 200),) * 3)
    for i in range(5):
        x0 = w * 0.08 + i * 40
        d.rectangle(
            (x0, h * 0.9, x0 + 40, h * 0.93), fill=(0, 0, 0) if i % 2 == 0 else (240, 240, 240)
        )
    d.polygon(
        [(w * 0.92, h * 0.08), (w * 0.9, h * 0.16), (w * 0.94, h * 0.16)], fill=(250, 250, 250)
    )
    return img.filter(ImageFilter.GaussianBlur(0.8))


def plan_image(size=(1500, 1900)) -> Image.Image:
    """A field plan: trench outline, features, labels and a title block, all inside the image."""
    w, h = size
    img = Image.new("L", size, 255)
    d = ImageDraw.Draw(img)
    label = ImageFont.truetype(font_path("DejaVuSans.ttf"), 34)
    small = ImageFont.truetype(font_path("DejaVuSans.ttf"), 26)
    d.rectangle((150, 200, 1350, 1350), outline=0, width=6)
    d.text((170, 215), "Sonda II", font=label, fill=0)
    for i, (x, y, rx, ry) in enumerate(
        [(450, 600, 160, 110), (900, 520, 120, 120), (700, 1050, 220, 130)]
    ):
        d.ellipse((x - rx, y - ry, x + rx, y + ry), outline=0, width=4)
        for k in range(-rx, rx, 22):
            d.line((x + k, y - ry * 0.6, x + k + 30, y + ry * 0.6), fill=110, width=2)
        d.text((x - 60, y + ry + 10), f"Objekt {i + 1}", font=label, fill=0)
    d.line((1250, 260, 1250, 380), fill=0, width=5)
    d.polygon([(1250, 230), (1232, 280), (1268, 280)], fill=0)
    d.text((1238, 390), "S", font=label, fill=0)
    for i in range(4):
        d.rectangle(
            (200 + i * 100, 1420, 300 + i * 100, 1440), fill=0 if i % 2 == 0 else 255, outline=0
        )
        d.text((192 + i * 100, 1450), str(i), font=small, fill=0)
    d.text((600, 1450), "m", font=small, fill=0)
    d.rectangle((150, 1560, 1350, 1800), outline=0, width=4)
    for i, row in enumerate(
        [
            "Hradiště u Horní Mezí, akce A123/2024",
            "Sonda II — plán objektů 1–3, M 1:50",
            "Kreslila: E. Procházková, 9. 7. 2024",
        ]
    ):
        d.text((180, 1585 + i * 70), row, font=label, fill=0)
    return img


def typewritten_scan(lines: list[str], seed: int, stamp_room: bool = False) -> Image.Image:
    """A greyish typewritten page at 150 dpi, slightly rotated, with speckles."""
    rng = random.Random(seed)
    w, h = 1240, 1754
    img = Image.new("L", (w, h), 236)
    d = ImageDraw.Draw(img)
    mono = ImageFont.truetype(font_path("DejaVuSansMono.ttf"), 25)
    y = 190
    for line in lines:
        d.text((150 + rng.randint(-2, 2), y), line, font=mono, fill=rng.randint(30, 60))
        y += 46
    for _ in range(900):
        x, yy = rng.randrange(w), rng.randrange(h)
        d.point((x, yy), fill=rng.randint(120, 200))
    img = img.rotate(0.35, resample=Image.BICUBIC, fillcolor=236)
    return img.filter(ImageFilter.GaussianBlur(0.6))


def sketch_scan(seed: int) -> Image.Image:
    rng = random.Random(seed)
    img = Image.new("L", (1240, 1754), 238)
    d = ImageDraw.Draw(img)
    pts = [(300, 400), (950, 380), (980, 1200), (280, 1240)]
    for a, b in zip(pts, pts[1:] + pts[:1], strict=True):
        d.line((a, b), fill=40, width=4)
    for _ in range(3):
        x, y = rng.randint(420, 820), rng.randint(520, 1050)
        d.ellipse((x - 90, y - 60, x + 90, y + 60), outline=50, width=3)
    for _ in range(700):
        d.point((rng.randrange(1240), rng.randrange(1754)), fill=rng.randint(120, 200))
    return img.rotate(-0.5, resample=Image.BICUBIC, fillcolor=238).filter(
        ImageFilter.GaussianBlur(0.7)
    )


def image_reader(img: Image.Image, fmt: str = "JPEG") -> ImageReader:
    buf = io.BytesIO()
    if fmt == "JPEG":
        img.convert("RGB" if img.mode == "RGB" else "L").save(
            buf, "JPEG", quality=72, optimize=True
        )
    else:
        img.save(buf, "PNG", optimize=True)
    buf.seek(0)
    return ImageReader(buf)


# ── CTX000000004: the born-digital report ───────────────────────────────────────────────────

INTRO = [
    "Záchranný výzkum proběhl v červnu a červenci 2024 na jižním svahu hradiště u "
    "Horní Mezí, v místě plánované přístavby hospodářské budovy. Plocha stavby měřila 420 m²; "
    "výzkum zachytil její severní část v rozsahu dvou sond.",
    "Lokalita leží na ostrožně nad soutokem dvou potoků v nadmořské výšce 412 m. Valové těleso je v "
    "terénu patrné jen na severní straně, jižní část byla v minulosti rozorána.",
]
METHOD = [
    "Ornice byla odstraněna mechanizací pod dohledem archeologa. Objekty byly vybírány po "
    "polovinách, výplň byla prosívána. Kresebná dokumentace vznikla v měřítku 1:20 a 1:50, poloha "
    "sond byla zaměřena totální stanicí v systému S-JTSK.",
]
COLUMN_LEFT = [
    "Sonda I (5 × 4 m) zachytila pod ornicí hnědou hlinitou vrstvu o mocnosti 30–40 cm, která "
    "obsahovala drobné zlomky keramiky a mazanice. Pod ní se rýsovalo podloží tvořené žlutou "
    "sprašovou hlínou.",
    "V západní části sondy se objevil mělký žlab orientovaný ve směru S–J, jehož výplň tvořila "
    "tmavá humózní hlína s uhlíky.",
]
COLUMN_RIGHT = [
    "Sonda II (6 × 5 m) byla položena k úpatí valu. Zachytila tři zahloubené objekty, z nichž "
    "objekt 3 byl nejrozsáhlejší a obsahoval vrstvu propáleného kamene.",
    "Mocnost kulturní vrstvy činila v sondě II až 40 cm. Ve vrstvě se nacházely zlomky nádob, "
    "zvířecí kosti a několik železných předmětů.",
]
TABLE = [
    ["Objekt", "Sonda", "Typ", "Rozměry (cm)", "Hloubka (cm)", "Datace"],
    ["1", "II", "jáma", "160 × 110", "45", "raný středověk"],
    ["2", "II", "kůlová jamka", "40 × 38", "30", "raný středověk"],
    ["3", "II", "zahloubená stavba", "320 × 260", "70", "9.–10. stol."],
    ["4", "I", "žlab", "450 × 35", "18", "nedatováno"],
    ["5", "I", "kůlová jamka", "36 × 34", "22", "nedatováno"],
]
FINDS = [
    "Soubor nálezů tvoří 214 zlomků keramiky, 36 zvířecích kostí, 3 železné předměty a vzorky "
    "uhlíků. Keramika je převážně hrnčířská, s vlnicí a rytými liniemi na plecích, a pochází z "
    "raného středověku.",
    "Z výplně objektu 3 pochází železný nůž s řapem a přeslen z jemně plavené hlíny. Soubor nálezů "
    "byl uložen v depozitáři pod přírůstkovým číslem A123/2024.",
]
CONCLUSION = [
    "Výzkum potvrdil osídlení jižního svahu hradiště v raném středověku. Zahloubená stavba v sondě "
    "II je zatím jediným dokladem obytné zástavby mimo opevněnou plochu.",
]


def report_pdf() -> bytes:
    buf = io.BytesIO()
    c = new_canvas(buf, "Hradiště u Horní Mezí: nálezová zpráva 2024")
    c.addPageLabel(0, style="ROMAN_LOWER")  # i, ii
    c.addPageLabel(2, style="ARABIC", start=1)  # 1, 2, …

    # i — title page
    c.setFont("Sans", 11)
    c.drawCentredString(W / 2, H - 120, "Archeologická společnost DEMO, z. s.")
    c.setFont("Sans-Bold", 26)
    c.drawCentredString(W / 2, H - 300, "Hradiště u Horní Mezí")
    c.setFont("Sans", 15)
    c.drawCentredString(W / 2, H - 335, "Nálezová zpráva ze záchranného výzkumu, sezóna 2024")
    c.setFont("Serif", 12)
    for i, row in enumerate(
        ["Akce č. A123/2024", "Zpracoval: Jan Novotný", "Spolupracovala: Eva Procházková"]
    ):
        c.drawCentredString(W / 2, H - 420 - i * 20, row)
    c.drawCentredString(W / 2, 110, "Horní Mez, prosinec 2024")
    c.showPage()

    # ii — contents
    c.setFont("Sans-Bold", 16)
    c.drawString(MARGIN, H - 110, "Obsah")
    c.setFont("Serif", 11)
    toc = [
        ("1. Poloha lokality a důvod výzkumu", "1"),
        ("2. Metodika výzkumu", "1"),
        ("3. Popis zjištěných situací", "2"),
        ("4. Soupis objektů", "3"),
        ("5. Nálezy", "6"),
        ("6. Závěr", "6"),
        ("Příloha 1: Fotodokumentace", "7"),
    ]
    for i, (entry, page) in enumerate(toc):
        y = H - 150 - i * 22
        c.drawString(MARGIN, y, entry)
        c.drawRightString(W - MARGIN, y, page)
        x = MARGIN + pdfmetrics.stringWidth(entry, "Serif", 11) + 6
        c.drawString(
            x, y, "." * int((W - MARGIN - 16 - x) / pdfmetrics.stringWidth(".", "Serif", 11))
        )
    c.showPage()

    # 1 — introduction and method
    furniture(c, "1")
    y = heading(c, "1. Poloha lokality a důvod výzkumu", H - 90)
    for p in INTRO:
        y = paragraph(c, p, MARGIN, y, W - 2 * MARGIN)
    y = heading(c, "2. Metodika výzkumu", y - 10)
    for p in METHOD:
        y = paragraph(c, p, MARGIN, y, W - 2 * MARGIN)
    c.showPage()

    # 2 — two columns
    furniture(c, "2")
    y0 = heading(c, "3. Popis zjištěných situací", H - 90)
    col_w = (W - 2 * MARGIN - 24) / 2
    for x, texts in [(MARGIN, COLUMN_LEFT), (MARGIN + col_w + 24, COLUMN_RIGHT)]:
        y = y0
        for p in texts:
            y = paragraph(c, p, x, y, col_w, indent=12)
    c.showPage()

    # 3 — ruled table
    furniture(c, "3")
    y = heading(c, "4. Soupis objektů", H - 90)
    c.setFont("Serif-Italic", 10)
    c.drawString(MARGIN, y, "Tab. 1. Soupis objektů ze sond I a II.")
    widths = [50, 45, 120, 85, 75, 0]
    widths[-1] = W - 2 * MARGIN - sum(widths)
    top, row_h = y - 12, 20
    c.setLineWidth(0.6)
    for r, row in enumerate(TABLE):
        x = MARGIN
        c.setFont("Serif-Bold" if r == 0 else "Serif", 10)
        for cell, cw in zip(row, widths, strict=True):
            c.rect(x, top - (r + 1) * row_h, cw, row_h)
            c.drawString(x + 4, top - (r + 1) * row_h + 6, cell)
            x += cw
    c.showPage()

    # 4 — photograph with a letter-spaced caption
    furniture(c, "4")
    c.drawImage(
        image_reader(photo_image(4)), MARGIN, 330, W - 2 * MARGIN, (W - 2 * MARGIN) * 860 / 1200
    )
    t = c.beginText(MARGIN, 300)
    t.setFont("Serif-Italic", 10)
    t.setCharSpace(2.6)
    t.textLine("Obr. 1. Sonda II, pohled na objekt 3 od jihu.")
    c.drawText(t)
    c.showPage()

    # 5 — plan: labels inside the image, only the running header and page number as text
    furniture(c, "5")
    c.drawImage(image_reader(plan_image(), "PNG"), 15, 15, W - 30, H - 62)
    c.showPage()

    # 6 — finds, conclusion, literature
    furniture(c, "6")
    y = heading(c, "5. Nálezy", H - 90)
    for p in FINDS:
        y = paragraph(c, p, MARGIN, y, W - 2 * MARGIN)
    y = heading(c, "6. Závěr", y - 10)
    for p in CONCLUSION:
        y = paragraph(c, p, MARGIN, y, W - 2 * MARGIN)
    y = heading(c, "Literatura", y - 10)
    for ref in [
        "Novotný, J. 2021: Hradiště u Horní Mezí. Zpravodaj DEMO 7, 11–19.",
        "Procházková, E. 2023: Keramika z ostrožny nad Horní Mezí. Sborník DEMO 2, 45–60.",
    ]:
        y = paragraph(c, ref, MARGIN, y, W - 2 * MARGIN, size=10, leading=13, indent=0)
    c.showPage()

    # 7 — appendix photograph with a short caption
    furniture(c, "7")
    c.setFont("Sans-Bold", 12)
    c.drawString(MARGIN, H - 90, "Příloha 1: Fotodokumentace")
    c.drawImage(
        image_reader(photo_image(7)), MARGIN, 300, W - 2 * MARGIN, (W - 2 * MARGIN) * 860 / 1200
    )
    c.setFont("Serif-Italic", 10)
    c.drawString(MARGIN, 280, "Obr. 2. Sonda I, západní profil s vrstvou hnědé hlinité výplně.")
    c.showPage()
    c.save()
    return buf.getvalue()


# ── CTX000000005: a scan in a PDF ───────────────────────────────────────────────────────────

SCAN_PAGES = [
    [
        "Muzeum DEMO v Horní Mezi",
        "",
        "HLÁŠENÍ O ARCHEOLOGICKÉM NÁLEZU",
        "",
        "Lokalita: Horní Mez, poloha Na Hradišti",
        "Datum nálezu: 14. 5. 1998",
        "",
        "Při výkopu jámy pro sloup elektrického vedení",
        "byla v hloubce 60 cm zachycena tmavá vrstva",
        "s uhlíky a zlomky keramiky. Nález ohlásil",
        "stavbyvedoucí, místo ohledala Eva Procházková.",
    ],
    [
        "Popis nálezové situace:",
        "",
        "Ve stěně výkopu byl patrný profil zahloubeného",
        "objektu o šířce 1,2 m. Výplň tvořila tmavě hnědá",
        "hlína s propáleným kamenem. Ze stěny bylo",
        "vyzvednuto 12 zlomků keramiky a 2 zvířecí kosti.",
        "",
        "Nálezy předány do muzea pod číslem 45/1998.",
    ],
    None,  # the sketch
    [
        "Seznam příloh:",
        "",
        "1. Náčrt situace výkopu (1 list)",
        "2. Soupis nálezů (1 list)",
        "",
        "V Horní Mezi dne 20. 5. 1998",
        "Eva Procházková",
    ],
]


def scan_pdf() -> bytes:
    buf = io.BytesIO()
    c = new_canvas(buf, "Hlášení o archeologickém nálezu, Horní Mez 1998 (sken)")
    for i, lines in enumerate(SCAN_PAGES):
        img = sketch_scan(50 + i) if lines is None else typewritten_scan(lines, 50 + i)
        c.drawImage(image_reader(img), 0, 0, W, H)
        if i in (0, 1):
            # A prior OCR run's text: invisible (render mode 3), roughly where the image has it.
            t = c.beginText()
            t.setTextRenderMode(3)
            t.setFont("Sans", 12)
            for k, line in enumerate(lines):
                if line:
                    t.setTextOrigin(150 * 72 / 150, H - (190 + 46 * k + 22) * 72 / 150)
                    t.textOut(line)
            c.drawText(t)
        if i == 3:
            # The page number a PDF editor stamped over the scan: the page's only text.
            c.setFont("Sans", 10)
            c.drawCentredString(W / 2, 24, "4")
        c.showPage()
    c.save()
    return buf.getvalue()


# ── CTX000000006: text layers that do not decode ────────────────────────────────────────────

CLEAN = (
    "Antropologický posudek k hrobům z výzkumu 2024 na hradišti u Horní Mezí. Kostry byly "
    "vyzvednuty z hrobových jam v sondě II a uloženy v depozitáři pod číslem A123/2024."
)
DAMAGED = (
    "Při čištění sondy byly zjištěny tři hrobové jámy s tmavou výplní. Hrob 1 obsahoval kostru "
    "dospělého jedince uloženou na zádech, hlavou k západu. Ve hrobě 2 byly zbytky dětské kostry, "
    "hrob 3 byl porušen mladším zahloubením. Kosti jsou křehké a místy chybí."
)
UNMAPPED = (
    "Hrob 1: muž, 35–45 let, výška postavy asi 172 cm. Na lebce jsou patrné stopy zhojeného "
    "zranění. Hrob 2: dítě, 4–6 let. Hrob 3: jedinec neurčeného pohlaví, dospělý."
)
MOJIBAKE = (
    "Závěr: soubor je malý, ale potvrzuje pohřbívání na jižním svahu hradiště. Přesnější datování "
    "umožní radiouhlíkové stáří vzorků z hrobů 1 a 3."
)
#: The glyphs page 2's font gives no Unicode for (its ToUnicode CMap leaves them out).
LOST = {"0159", "0158", "011B", "011A", "010D", "010C", "016F", "016E"}


def garbled_pdf() -> bytes:
    buf = io.BytesIO()
    c = new_canvas(buf, "Hradiště u Horní Mezí: antropologický posudek (text layers)")
    pages = [
        ("DejaVuSerif", "1. Úvod", CLEAN),
        ("Serif", "2. Hrobové jámy", DAMAGED),
        ("Sans", "3. Určení", UNMAPPED),
    ]
    for n, (font, title, text) in enumerate(pages, start=1):
        c.setFont("DejaVuSerif-Bold", 13)
        c.drawString(MARGIN, H - 100, title)
        paragraph(c, text, MARGIN, H - 130, W - 2 * MARGIN, font=font, size=11.5, leading=16)
        c.setFont("DejaVuSerif", 10)
        c.drawCentredString(W / 2, 32, str(n))
        c.showPage()
    # Page 4: CP1250 bytes under the base-14 WinAnsi font, as a producer that mislabels its encoding.
    c.setFont("DejaVuSerif-Bold", 13)
    c.drawString(MARGIN, H - 100, "4. Závěr")
    mislabelled = MOJIBAKE.encode("cp1250").decode("cp1252")
    paragraph(
        c, mislabelled, MARGIN, H - 130, W - 2 * MARGIN, font="Helvetica", size=11, leading=16
    )
    c.setFont("DejaVuSerif", 10)
    c.drawCentredString(W / 2, 32, "4")
    c.showPage()
    c.save()

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(buf.getvalue())))
    seen = set()
    for page in writer.pages:
        for ref in page["/Resources"]["/Font"].values():
            font = ref.get_object()
            base = str(font["/BaseFont"])
            if base in seen:
                continue
            seen.add(base)
            if base.endswith("+LiberationSerif"):
                _drop_mappings(font, LOST)
            elif base.endswith("+LiberationSans"):
                _map_all_to_fffd(font)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


_BFCHAR = re.compile(r"^<([0-9A-F]{2})> <([0-9A-F]{4})>$")


def _drop_mappings(font, lost: set[str]) -> None:
    cmap = font["/ToUnicode"].get_object()
    lines = cmap.get_data().decode("ascii").splitlines()
    kept = [ln for ln in lines if not (_BFCHAR.match(ln) and _BFCHAR.match(ln).group(2) in lost)]
    count = sum(1 for ln in kept if _BFCHAR.match(ln))
    cmap.set_data(
        "\n".join(re.sub(r"^\d+ beginbfchar$", f"{count} beginbfchar", ln) for ln in kept).encode()
    )


def _map_all_to_fffd(font) -> None:
    cmap = font["/ToUnicode"].get_object()
    lines = cmap.get_data().decode("ascii").splitlines()
    out = []
    for ln in lines:
        m = _BFCHAR.match(ln)
        out.append(f"<{m.group(1)}> <FFFD>" if m and m.group(2) not in ("0000", "0020") else ln)
    cmap.set_data("\n".join(out).encode())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--out", type=Path, default=HERE, help="output folder (default: beside this file)"
    )
    args = ap.parse_args()
    register_fonts()
    args.out.mkdir(parents=True, exist_ok=True)
    for name, build in [
        ("CTX000000004", report_pdf),
        ("CTX000000005", scan_pdf),
        ("CTX000000006", garbled_pdf),
    ]:
        data = build()
        (args.out / f"{name}.pdf").write_bytes(data)
        print(f"{name}.pdf  {len(PdfReader(io.BytesIO(data)).pages)} pages  {len(data):,} bytes")


if __name__ == "__main__":
    main()
