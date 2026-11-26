"""
Create: turn an answer into a Word document or a PowerPoint deck.

Every document carries its classification — the most sensitive cell the
answer drew on, taken from the question's audit record (catalog_ask_log),
never from the browser — in the page header and footer (Word) or a band
on every slide (PowerPoint), coloured by level like the rest of the app.

PDF and email are produced in the browser (a print-ready view and a
composed message) and only logged here — so Arabic text renders exactly
as on screen with no fonts to ship.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

from shared.brand import brand

MAX_RESULT_SETS = 10
MAX_ROWS = 500
MAX_COLUMNS = 12
_ARABIC = re.compile(r"[؀-ۿ]")

_T = {
    "en": {
        "answer": "Answer", "asked_by": "Asked by", "asked_on": "Asked on", "trust": "Trust",
        "reference": "Reference", "database": "Database", "data": "Data", "tables_used": "Tables used",
        "queries": "Queries run", "rows": "rows", "generated": "Created with " + brand()["name"],
        "no_label": "NO CLASSIFIED DATA", "question": "Question", "continued": "continued",
        "no_rows": "No rows returned.",
    },
    "ar": {
        "answer": "الإجابة", "asked_by": "طرح السؤال", "asked_on": "التاريخ", "trust": "الثقة",
        "reference": "المرجع", "database": "قاعدة البيانات", "data": "البيانات", "tables_used": "الجداول المستخدمة",
        "queries": "الاستعلامات المنفذة", "rows": "صفوف", "generated": "أُنشئ بواسطة " + brand().get("name_ar", brand()["name"]),
        "no_label": "لا توجد بيانات مصنّفة", "question": "السؤال", "continued": "تابع",
        "no_rows": "لم تُرجع أي صفوف.",
    },
}


@dataclass
class Marking:
    """The classification stamped on the document."""
    text: str                 # e.g. "RESTRICTED · 40D · Ops Restricted"
    color: str = "#6B7280"    # level colour, hex


@dataclass
class ResultSet:
    purpose: str
    columns: List[str]
    rows: List[List[Optional[str]]]


@dataclass
class ExportDoc:
    question: str
    answer: Optional[str]
    marking: Marking
    asked_by: Optional[str]
    asked_at: Optional[datetime]
    ask_id: str
    lang: str = "en"
    database_name: Optional[str] = None
    trust_label: Optional[str] = None
    trust_score: Optional[float] = None
    results: List[ResultSet] = field(default_factory=list)
    tables: List[str] = field(default_factory=list)
    queries: List[Tuple[Optional[str], str, Optional[int]]] = field(default_factory=list)  # (purpose, sql, rows)

    @property
    def t(self):
        return _T.get(self.lang, _T["en"])

    @property
    def trust_text(self) -> str:
        if not self.trust_label:
            return "—"
        return f"{self.trust_label.title()}" + (f" ({round(self.trust_score * 100)}%)" if self.trust_score is not None else "")

    def meta(self) -> List[Tuple[str, str]]:
        t = self.t
        rows = [
            (t["asked_by"], self.asked_by or "—"),
            (t["asked_on"], self.asked_at.strftime("%Y-%m-%d %H:%M") if self.asked_at else "—"),
        ]
        if self.database_name:
            rows.append((t["database"], self.database_name))
        rows += [(t["trust"], self.trust_text), (t["reference"], self.ask_id[:8])]
        return rows


def clamp_results(results: Sequence) -> List[ResultSet]:
    out = []
    for r in list(results)[:MAX_RESULT_SETS]:
        cols = list(r.columns)[:MAX_COLUMNS]
        rows = [list(row)[:MAX_COLUMNS] for row in list(r.rows)[:MAX_ROWS]]
        out.append(ResultSet(purpose=r.purpose or "", columns=cols, rows=rows))
    return out


def _hex(color: str) -> Tuple[int, int, int]:
    c = (color or "#6B7280").lstrip("#")
    if len(c) != 6:
        c = "6B7280"
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def is_rtl(text: Optional[str]) -> bool:
    return bool(text and _ARABIC.search(text))


# ---------------------------------------------------------------------------
# Email draft and print-ready page (for callers without their own renderer)
# ---------------------------------------------------------------------------

def build_email(doc: ExportDoc) -> Tuple[str, str]:
    t = doc.t
    lines = [doc.marking.text, "", f"{t['question']}: {doc.question}", "", doc.answer or ""]
    rs = next((r for r in doc.results if r.rows), None)
    if rs:
        lines += ["", f"{rs.purpose or t['data']}:", " | ".join(rs.columns)]
        lines += [" | ".join("" if v is None else str(v) for v in row) for row in rs.rows[:10]]
        if len(rs.rows) > 10:
            lines.append(f"… +{len(rs.rows) - 10} {t['rows']}")
    if doc.tables:
        lines += ["", f"{t['tables_used']}: {', '.join(doc.tables)}"]
    lines += [f"{k}: {v}" for k, v in doc.meta()]
    lines += ["", doc.marking.text]
    return f"[{doc.marking.text}] {doc.question}", "\n".join(lines)


def build_html(doc: ExportDoc) -> str:
    from html import escape as e

    t = doc.t
    tables = "".join(
        f"<h2 dir='auto'>{e(rs.purpose or t['data'])}</h2><table><thead><tr>"
        + "".join(f"<th>{e(c)}</th>" for c in rs.columns) + "</tr></thead><tbody>"
        + "".join("<tr>" + "".join(f"<td dir='auto'>{e('' if v is None else str(v))}</td>" for v in row) + "</tr>"
                  for row in rs.rows) + "</tbody></table>"
        for rs in doc.results if rs.rows)
    meta = "".join(f"<span><b>{e(k)}:</b> {e(v)}</span>" for k, v in doc.meta())
    sources = (f"<h2>{e(t['tables_used'])}</h2><p class='src'>{e(' · '.join(doc.tables))}</p>" if doc.tables else "")
    color = doc.marking.color
    return f"""<!doctype html><html lang="{doc.lang}" dir="{'rtl' if doc.lang == 'ar' else 'ltr'}"><head><meta charset="utf-8">
<title>{e(doc.question)}</title><style>
@page {{ margin: 22mm 16mm; }}
body {{ font-family: "Segoe UI", Tahoma, Arial, sans-serif; color: #0f172a; font-size: 11pt; margin: 0 auto; max-width: 900px; padding: 12px; }}
.mark {{ text-align: center; font-weight: 700; font-size: 9pt; color: {color}; letter-spacing: .04em; }}
@media print {{ .mark {{ position: fixed; left: 0; right: 0; }} .mark.top {{ top: -14mm; }} .mark.bottom {{ bottom: -14mm; }} }}
h1 {{ font-size: 18pt; margin: 8px 0 6px; }} h2 {{ font-size: 12.5pt; margin: 20px 0 8px; }}
.meta {{ color: #475569; font-size: 9pt; display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 14px; }}
.answer {{ font-size: 12pt; line-height: 1.6; white-space: pre-wrap; border-inline-start: 3px solid {color}; padding-inline-start: 12px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 9.5pt; }} th, td {{ border: 1px solid #cbd5e1; padding: 4px 6px; text-align: start; }}
th {{ background: #f1f5f9; }} .src {{ color: #475569; font-size: 9.5pt; }} .foot {{ margin-top: 24px; color: #94a3b8; font-size: 8.5pt; }}
</style></head><body>
<div class="mark top">{e(doc.marking.text)}</div><div class="mark bottom">{e(doc.marking.text)}</div>
<h1 dir="auto">{e(doc.question)}</h1><div class="meta">{meta}</div>
<div class="answer" dir="auto">{e(doc.answer or '')}</div>{tables}{sources}
<div class="foot">{e(t['generated'])}</div>
</body></html>"""


# ---------------------------------------------------------------------------
# Word
# ---------------------------------------------------------------------------

def build_docx(doc: ExportDoc) -> bytes:
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    t = doc.t
    rtl_doc = doc.lang == "ar"
    color = RGBColor(*_hex(doc.marking.color))
    d = Document()
    normal = d.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)

    def rtl(p, force: bool = False):
        """Right-to-left only for text that IS Arabic (marking an English
        sentence RTL flips its punctuation); English in an Arabic document
        is just right-aligned."""
        if force or is_rtl(p.text):
            p_pr = p._p.get_or_add_pPr()
            p_pr.append(OxmlElement("w:bidi"))
            for run in p.runs:
                r_pr = run._r.get_or_add_rPr()
                r_pr.append(OxmlElement("w:rtl"))
                if run.font.size is not None:  # complex-script size = the run's size
                    sz = OxmlElement("w:szCs")
                    sz.set(qn("w:val"), str(int(run.font.size.pt * 2)))
                    r_pr.append(sz)
        elif rtl_doc and p.alignment is None:
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        return p

    def marking_para(container):
        p = container.paragraphs[0] if container.paragraphs else container.add_paragraph()
        p.text = ""
        run = p.add_run(doc.marking.text)
        run.bold = True
        run.font.size = Pt(9)
        run.font.color.rgb = color
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        rtl(p)

    section = d.sections[0]
    marking_para(section.header)
    marking_para(section.footer)
    foot = section.footer.add_paragraph()
    frun = foot.add_run(f"{t['generated']} · {t['reference']} {doc.ask_id[:8]}")
    frun.font.size = Pt(8)
    frun.font.color.rgb = RGBColor(0x6B, 0x72, 0x80)
    foot.alignment = WD_ALIGN_PARAGRAPH.CENTER

    title = d.add_heading(doc.question, level=1)
    rtl(title)

    meta = d.add_table(rows=0, cols=2)
    meta.style = "Table Grid"
    for k, v in doc.meta():
        cells = meta.add_row().cells
        cells[0].text, cells[1].text = k, v
        for run in cells[0].paragraphs[0].runs:
            run.bold = True
        _shade(cells[0], "F1F5F9")
        for c in cells:
            for p in c.paragraphs:
                for run in p.runs:
                    run.font.size = Pt(9.5)
                rtl(p)
    if rtl_doc:
        _table_rtl(meta)

    h = d.add_heading(t["answer"], level=2)
    rtl(h)
    for para in (doc.answer or "").split("\n"):
        if para.strip():
            rtl(d.add_paragraph(para.strip()))

    for rs in doc.results:
        h = d.add_heading(rs.purpose or t["data"], level=2)
        rtl(h)
        if not rs.rows:
            rtl(d.add_paragraph(t["no_rows"]))
            continue
        table = d.add_table(rows=1, cols=len(rs.columns))
        table.style = "Light Grid Accent 1"
        _look(table, first_row=True, first_column=False)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        for i, name in enumerate(rs.columns):
            cell = table.rows[0].cells[i]
            cell.text = name
            for run in cell.paragraphs[0].runs:
                run.bold = True
        for row in rs.rows:
            cells = table.add_row().cells
            for i, v in enumerate(row):
                cells[i].text = "" if v is None else str(v)
                for p in cells[i].paragraphs:
                    for run in p.runs:
                        run.font.size = Pt(9)
                    if is_rtl(p.text):
                        rtl(p)
        if rtl_doc:
            _table_rtl(table)

    if doc.tables:
        h = d.add_heading(t["tables_used"], level=2)
        rtl(h)
        for name in doc.tables:
            p = d.add_paragraph(name, style="List Bullet")

    if doc.queries:
        h = d.add_heading(t["queries"], level=2)
        rtl(h)
        for purpose, sql, rows in doc.queries:
            label = d.add_paragraph()
            r = label.add_run(f"{purpose or ''}" + (f" — {rows} {t['rows']}" if rows is not None else ""))
            r.bold = True
            code = d.add_paragraph()
            cr = code.add_run(sql)
            cr.font.name = "Consolas"
            cr.font.size = Pt(8.5)

    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _shade(cell, hex_fill: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    cell._tc.get_or_add_tcPr().append(shd)


def _look(table, first_row: bool, first_column: bool) -> None:
    """Which parts of the table style apply (header row yes, bold first
    column no)."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tbl_pr = table._tbl.tblPr
    look = tbl_pr.find(qn("w:tblLook"))
    if look is None:
        look = OxmlElement("w:tblLook")
        tbl_pr.append(look)
    val = (0x0020 if first_row else 0) | (0x0080 if first_column else 0) | 0x0400
    look.set(qn("w:val"), f"{val:04X}")
    look.set(qn("w:firstRow"), "1" if first_row else "0")
    look.set(qn("w:firstColumn"), "1" if first_column else "0")
    look.set(qn("w:noVBand"), "1")


def _table_rtl(table) -> None:
    from docx.oxml import OxmlElement

    tbl_pr = table._tbl.tblPr
    tbl_pr.append(OxmlElement("w:bidiVisual"))


# ---------------------------------------------------------------------------
# PowerPoint
# ---------------------------------------------------------------------------

ROWS_PER_SLIDE = 12
MAX_SLIDE_COLUMNS = 8


def build_pptx(doc: ExportDoc) -> bytes:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Emu, Inches, Pt

    t = doc.t
    rtl_doc = doc.lang == "ar"
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]
    band = RGBColor(*_hex(doc.marking.color))
    ink = RGBColor(0x0F, 0x17, 0x2A)
    muted = RGBColor(0x6B, 0x72, 0x80)
    W, H = prs.slide_width, prs.slide_height

    def set_rtl(paragraph, text: str, ltr: bool = False):
        if ltr:
            return
        if is_rtl(text):
            paragraph._p.get_or_add_pPr().set("rtl", "1")
            paragraph.alignment = PP_ALIGN.RIGHT
        elif rtl_doc:
            paragraph.alignment = PP_ALIGN.RIGHT

    def text_box(slide, x, y, w, h, text, size=14, bold=False, color=ink, align=None, font=None, ltr=False):
        box = slide.shapes.add_textbox(x, y, w, h)
        tf = box.text_frame
        tf.word_wrap = True
        lines = text.split("\n") if text else [""]
        for i, line in enumerate(lines):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            run = p.add_run()
            run.text = line
            run.font.size = Pt(size)
            run.font.bold = bold
            run.font.color.rgb = color
            if font:
                run.font.name = font
            if align is not None:
                p.alignment = align
            set_rtl(p, line, ltr=ltr)
        return box

    def marking(slide):
        for y in (0, H - Inches(0.36)):
            shape = slide.shapes.add_shape(1, 0, y, W, Inches(0.36))  # rectangle
            shape.fill.solid()
            shape.fill.fore_color.rgb = band
            shape.line.fill.background()
            tf = shape.text_frame
            tf.paragraphs[0].alignment = PP_ALIGN.CENTER
            run = tf.paragraphs[0].add_run()
            run.text = doc.marking.text
            run.font.size = Pt(11)
            run.font.bold = True
            run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

    def new_slide(title: str):
        slide = prs.slides.add_slide(blank)
        marking(slide)
        text_box(slide, Inches(0.6), Inches(0.55), W - Inches(1.2), Inches(0.9), title, size=24, bold=True)
        return slide

    # 1. title
    slide = prs.slides.add_slide(blank)
    marking(slide)
    text_box(slide, Inches(0.8), Inches(2.2), W - Inches(1.6), Inches(1.8), doc.question, size=32, bold=True)
    meta = "   ·   ".join(f"{k}: {v}" for k, v in doc.meta())
    text_box(slide, Inches(0.8), Inches(4.3), W - Inches(1.6), Inches(0.8), meta, size=14, color=muted)
    text_box(slide, Inches(0.8), H - Inches(1.1), W - Inches(1.6), Inches(0.5), t["generated"], size=11, color=muted)

    # 2. answer
    slide = new_slide(t["answer"])
    text_box(slide, Inches(0.6), Inches(1.5), W - Inches(1.2), Inches(4.2), doc.answer or "—", size=18)
    if doc.tables:
        text_box(slide, Inches(0.6), H - Inches(1.35), W - Inches(1.2), Inches(0.8),
                 f"{t['tables_used']}: " + ", ".join(doc.tables), size=11, color=muted)

    # 3. data, 12 rows per slide
    for rs in doc.results:
        if not rs.rows:
            continue
        cols = rs.columns[:MAX_SLIDE_COLUMNS]
        chunks = [rs.rows[i:i + ROWS_PER_SLIDE] for i in range(0, len(rs.rows), ROWS_PER_SLIDE)][:10]
        for n, chunk in enumerate(chunks):
            title = rs.purpose or t["data"]
            if len(chunks) > 1:
                title += f" ({n + 1}/{len(chunks)})"
            slide = new_slide(title)
            top = Inches(1.5)
            height = Emu(int(Inches(0.38) * (len(chunk) + 1)))
            shape = slide.shapes.add_table(len(chunk) + 1, len(cols), Inches(0.6), top, W - Inches(1.2), height)
            table = shape.table
            for c, name in enumerate(cols):
                cell = table.cell(0, c)
                cell.text = name
                para = cell.text_frame.paragraphs[0]
                para.runs[0].font.size = Pt(12)
                para.runs[0].font.bold = True
            for r, row in enumerate(chunk, start=1):
                for c in range(len(cols)):
                    v = row[c] if c < len(row) else None
                    cell = table.cell(r, c)
                    cell.text = "" if v is None else str(v)
                    para = cell.text_frame.paragraphs[0]
                    if para.runs:
                        para.runs[0].font.size = Pt(11)
                    set_rtl(para, cell.text)

    # 4. queries (only when the asker may see SQL)
    if doc.queries:
        slide = new_slide(t["queries"])
        body = "\n\n".join(f"{p or ''}" + (f" — {r} {t['rows']}" if r is not None else "") + f"\n{sql}"
                           for p, sql, r in doc.queries[:6])
        text_box(slide, Inches(0.6), Inches(1.5), W - Inches(1.2), H - Inches(2.3), body, size=11, font="Consolas", ltr=True)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
