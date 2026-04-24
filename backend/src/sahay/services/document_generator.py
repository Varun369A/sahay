"""Document generator — agent-invoked creation of resumes, reports,
presentations, letters, and plain docs.

Flow (one call):
    1. Sonnet 4.6 receives the user's goal + their role + scrubbed context
       and returns STRUCTURED content as JSON matching a per-kind schema.
    2. A pure-Python renderer converts that JSON into bytes (PDF / PPTX /
       plain text).
    3. We upload to R2 using the same key convention as uploads/gen-images,
       insert into the `files` table so it shows up in the user's Files
       screen, and return (file_id, signed_url, friendly_name).

Design choices
──────────────
• **Single HTTP boundary.** The agent's `create_document` tool runs
  server-side inside `agent_engine.step()`. We avoid a polling
  architecture because the whole generation — Sonnet call + render +
  R2 upload — fits in 5–10 seconds, which is acceptable UX for a
  one-shot "make me a resume" action.
• **Structured intermediate.** Going LLM → JSON → renderer is far
  more reliable than asking an LLM to emit raw LaTeX / pptx XML.
  The JSON schema also lets us version the template independently
  of the model.
• **Role-aware content.** The user's profile role is threaded into
  the Sonnet prompt so "draft me a report" produces a doctor-style
  patient summary vs. an engineer-style architecture doc.
• **Fail softly.** On any error, the `create` entrypoint returns a
  GenerationError with an actionable message rather than raising —
  the agent can surface this to the user as a `speak`.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from sahay.db.models import File as FileRow
from sahay.services import r2_client
from sahay.services.claude_client import complete
from sahay.util.logging import get_logger

log = get_logger(__name__)


# ─── Types ───────────────────────────────────────────────────────────


DocKind = Literal["resume", "report", "presentation", "letter", "plain"]
DocFormat = Literal["pdf", "pptx", "txt"]

_KIND_TO_FORMAT: dict[DocKind, DocFormat] = {
    "resume": "pdf",
    "report": "pdf",
    "letter": "pdf",
    "presentation": "pptx",
    "plain": "txt",
}

_FORMAT_TO_MIME: dict[DocFormat, str] = {
    "pdf": "application/pdf",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "txt": "text/plain; charset=utf-8",
}

_FORMAT_TO_EXT: dict[DocFormat, str] = {
    "pdf": "pdf", "pptx": "pptx", "txt": "txt",
}


@dataclass(slots=True)
class GeneratedDoc:
    file_id: UUID
    r2_key: str
    signed_url: str
    name: str
    mime_type: str
    kind: DocKind
    doc_format: DocFormat
    tokens_used: int
    cost_inr_paise: int


class GenerationError(Exception):
    """Raised when content structuring or rendering fails. The message is
    safe to show to the user (no stack traces, no PII).
    """


# ─── Prompts ─────────────────────────────────────────────────────────


_RESUME_SCHEMA = """{
  "name": "Full Name",
  "headline": "One-line tagline (e.g. 'Senior Frontend Engineer, ex-Stripe')",
  "contact": {"email": "", "phone": "", "location": "", "links": [{"label":"", "url":""}]},
  "summary": "2–4 sentence professional summary.",
  "experience": [
    {"role": "Senior Engineer", "company": "Acme", "location": "Bangalore",
     "dates": "2022 — Present",
     "bullets": ["Shipped X that did Y, resulting in Z.", "..."]}
  ],
  "skills": ["Kotlin", "Compose", "Kubernetes", "..."],
  "education": [
    {"degree": "B.Tech Computer Science", "school": "IIT Madras", "dates": "2015 — 2019"}
  ],
  "awards": ["Optional list"]
}"""

_REPORT_SCHEMA = """{
  "title": "Report title",
  "subtitle": "Optional subtitle / author / date",
  "sections": [
    {"heading": "Introduction", "paragraphs": ["...", "..."]},
    {"heading": "Findings", "paragraphs": ["..."],
     "bullets": ["Key fact A", "Key fact B"]},
    {"heading": "Conclusion", "paragraphs": ["..."]}
  ]
}"""

_LETTER_SCHEMA = """{
  "sender": {"name": "", "address_lines": ["..."]},
  "date": "YYYY-MM-DD or natural",
  "recipient": {"name": "", "address_lines": ["..."]},
  "salutation": "Dear X,",
  "body_paragraphs": ["...", "..."],
  "closing": "Sincerely,",
  "signature": "Full Name"
}"""

_PRESENTATION_SCHEMA = """{
  "title": "Deck title",
  "subtitle": "Optional subtitle or presenter + date",
  "slides": [
    {"heading": "Section heading", "bullets": ["...", "..."],
     "notes": "Optional speaker notes for this slide"}
  ]
}"""

_PLAIN_SCHEMA = """{
  "title": "Optional title",
  "body": "Plain text body, use \\n\\n between paragraphs."
}"""


_SCHEMAS: dict[DocKind, str] = {
    "resume": _RESUME_SCHEMA,
    "report": _REPORT_SCHEMA,
    "letter": _LETTER_SCHEMA,
    "presentation": _PRESENTATION_SCHEMA,
    "plain": _PLAIN_SCHEMA,
}


def _system_prompt(kind: DocKind, role: str | None) -> str:
    role_note = ""
    if role:
        role_note = (
            f"\nThe user is a {role}. Tailor the content's tone, vocabulary, "
            "and examples accordingly. If the document is a resume/report/"
            "letter/presentation, use conventions typical of that profession."
        )
    return (
        "You turn a user's natural-language brief into STRUCTURED content "
        f"for a {kind} document. Your entire reply MUST be a single JSON "
        "object matching this schema exactly — no markdown fences, no "
        "preamble, no trailing prose:\n\n"
        f"{_SCHEMAS[kind]}\n\n"
        "Rules:\n"
        "  • Fabricate nothing that will read as factual claims the user "
        "    would be embarrassed by. When the brief is thin, invent only "
        "    generic placeholders the user can edit (names, dates).\n"
        "  • Keep content crisp. Resume bullets start with a strong verb. "
        "    Report paragraphs are tight. Letters are concise.\n"
        "  • For presentations, each slide should have 3–6 bullets max "
        "    (readable from the back of a room).\n"
        "  • Use Indian English spelling and conventions by default unless "
        "    the brief says otherwise.\n"
        "  • NEVER include live payment / UPI / bank-account details."
        f"{role_note}"
    )


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    m = _JSON_RE.search(stripped)
    if not m:
        raise GenerationError("Couldn't structure the document content.")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise GenerationError(f"Invalid JSON from model: {e}") from e
    if not isinstance(data, dict):
        raise GenerationError("Model returned non-object JSON.")
    return data


# ─── Rendering — PDF (reportlab) ─────────────────────────────────────


def _render_resume_pdf(data: dict[str, Any]) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
    )

    styles = getSampleStyleSheet()
    name = ParagraphStyle(
        "Name", parent=styles["Title"], fontSize=22, spaceAfter=2 * mm,
        alignment=0, textColor="#0b3b65",
    )
    headline = ParagraphStyle(
        "Headline", parent=styles["Normal"], fontSize=11,
        spaceAfter=4 * mm, textColor="#444",
    )
    h2 = ParagraphStyle(
        "H2", parent=styles["Heading2"], fontSize=12,
        spaceBefore=4 * mm, spaceAfter=2 * mm, textColor="#0b3b65",
    )
    body = ParagraphStyle("Body", parent=styles["Normal"], fontSize=10, leading=13)
    bullet = ParagraphStyle(
        "Bullet", parent=body, leftIndent=10, bulletIndent=0,
        spaceBefore=1, spaceAfter=1,
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=15 * mm,
        title=str(data.get("name") or "Resume"),
    )
    story: list[Any] = []

    story.append(Paragraph(str(data.get("name") or "Your Name"), name))
    if data.get("headline"):
        story.append(Paragraph(str(data["headline"]), headline))

    # Contact line
    contact = data.get("contact") or {}
    parts: list[str] = []
    for key in ("email", "phone", "location"):
        if contact.get(key):
            parts.append(str(contact[key]))
    for link in contact.get("links") or []:
        lbl = link.get("label") or link.get("url")
        if lbl:
            parts.append(str(lbl))
    if parts:
        story.append(Paragraph(" · ".join(parts), body))
        story.append(Spacer(1, 3 * mm))

    if data.get("summary"):
        story.append(Paragraph("Summary", h2))
        story.append(Paragraph(str(data["summary"]), body))

    if data.get("experience"):
        story.append(Paragraph("Experience", h2))
        for job in data["experience"]:
            line = f"<b>{job.get('role','')}</b>, {job.get('company','')}"
            if job.get("location"):
                line += f" — {job['location']}"
            if job.get("dates"):
                line += f"  &nbsp;&nbsp;<i>{job['dates']}</i>"
            story.append(Paragraph(line, body))
            for b in job.get("bullets") or []:
                story.append(Paragraph(f"• {b}", bullet))
            story.append(Spacer(1, 2 * mm))

    if data.get("skills"):
        story.append(Paragraph("Skills", h2))
        story.append(Paragraph(", ".join(str(s) for s in data["skills"]), body))

    if data.get("education"):
        story.append(Paragraph("Education", h2))
        for ed in data["education"]:
            line = f"<b>{ed.get('degree','')}</b>, {ed.get('school','')}"
            if ed.get("dates"):
                line += f"  &nbsp;&nbsp;<i>{ed['dates']}</i>"
            story.append(Paragraph(line, body))

    if data.get("awards"):
        story.append(Paragraph("Awards", h2))
        for a in data["awards"]:
            story.append(Paragraph(f"• {a}", bullet))

    doc.build(story)
    return buf.getvalue()


def _render_report_pdf(data: dict[str, Any]) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "T", parent=styles["Title"], fontSize=20, spaceAfter=3 * mm,
        textColor="#0b3b65",
    )
    subtitle = ParagraphStyle(
        "S", parent=styles["Normal"], fontSize=11, spaceAfter=6 * mm, textColor="#666",
    )
    h2 = ParagraphStyle(
        "H2", parent=styles["Heading2"], fontSize=13,
        spaceBefore=4 * mm, spaceAfter=2 * mm, textColor="#0b3b65",
    )
    body = ParagraphStyle("Body", parent=styles["Normal"], fontSize=10.5, leading=14.5)
    bullet = ParagraphStyle("Bullet", parent=body, leftIndent=12)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title=str(data.get("title") or "Report"),
    )
    story: list[Any] = []

    story.append(Paragraph(str(data.get("title") or "Report"), title))
    if data.get("subtitle"):
        story.append(Paragraph(str(data["subtitle"]), subtitle))

    for section in data.get("sections") or []:
        if section.get("heading"):
            story.append(Paragraph(str(section["heading"]), h2))
        for p in section.get("paragraphs") or []:
            story.append(Paragraph(str(p), body))
            story.append(Spacer(1, 2 * mm))
        for b in section.get("bullets") or []:
            story.append(Paragraph(f"• {b}", bullet))

    doc.build(story)
    return buf.getvalue()


def _render_letter_pdf(data: dict[str, Any]) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    styles = getSampleStyleSheet()
    body = ParagraphStyle("Body", parent=styles["Normal"], fontSize=11, leading=15)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=25 * mm, bottomMargin=20 * mm,
        title="Letter",
    )
    story: list[Any] = []

    sender = data.get("sender") or {}
    if sender.get("name"):
        story.append(Paragraph(f"<b>{sender['name']}</b>", body))
    for line in sender.get("address_lines") or []:
        story.append(Paragraph(str(line), body))
    story.append(Spacer(1, 6 * mm))

    if data.get("date"):
        story.append(Paragraph(str(data["date"]), body))
        story.append(Spacer(1, 6 * mm))

    recipient = data.get("recipient") or {}
    if recipient.get("name"):
        story.append(Paragraph(f"<b>{recipient['name']}</b>", body))
    for line in recipient.get("address_lines") or []:
        story.append(Paragraph(str(line), body))
    story.append(Spacer(1, 6 * mm))

    if data.get("salutation"):
        story.append(Paragraph(str(data["salutation"]), body))
        story.append(Spacer(1, 3 * mm))

    for p in data.get("body_paragraphs") or []:
        story.append(Paragraph(str(p), body))
        story.append(Spacer(1, 3 * mm))

    if data.get("closing"):
        story.append(Spacer(1, 5 * mm))
        story.append(Paragraph(str(data["closing"]), body))
    if data.get("signature"):
        story.append(Spacer(1, 10 * mm))
        story.append(Paragraph(f"<b>{data['signature']}</b>", body))

    doc.build(story)
    return buf.getvalue()


# ─── Rendering — PPTX (python-pptx) ──────────────────────────────────


def _render_presentation_pptx(data: dict[str, Any]) -> bytes:
    from pptx import Presentation
    from pptx.util import Pt

    prs = Presentation()                                # default 4:3 blank deck
    title_layout = prs.slide_layouts[0]                 # Title Slide
    bullets_layout = prs.slide_layouts[1]               # Title + Content

    # ── Title slide ──
    title_slide = prs.slides.add_slide(title_layout)
    title_slide.shapes.title.text = str(data.get("title") or "Presentation")
    if title_slide.placeholders and data.get("subtitle"):
        try:
            title_slide.placeholders[1].text = str(data["subtitle"])
        except (KeyError, IndexError):
            pass

    for slide in data.get("slides") or []:
        s = prs.slides.add_slide(bullets_layout)
        s.shapes.title.text = str(slide.get("heading") or "")

        # Bullet body
        body_shape = s.placeholders[1]
        tf = body_shape.text_frame
        tf.clear()
        first = True
        for b in slide.get("bullets") or []:
            p = tf.paragraphs[0] if first else tf.add_paragraph()
            p.text = str(b)
            p.level = 0
            if p.runs:
                p.runs[0].font.size = Pt(18)
            first = False

        # Speaker notes
        notes = slide.get("notes")
        if notes:
            s.notes_slide.notes_text_frame.text = str(notes)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


# ─── Rendering — plain text ──────────────────────────────────────────


def _render_plain_txt(data: dict[str, Any]) -> bytes:
    title = str(data.get("title") or "").strip()
    body = str(data.get("body") or "").strip()
    text = f"{title}\n{'=' * len(title)}\n\n{body}\n" if title else f"{body}\n"
    return text.encode("utf-8")


_RENDERERS = {
    "resume":       (_render_resume_pdf,       "pdf"),
    "report":       (_render_report_pdf,       "pdf"),
    "letter":       (_render_letter_pdf,       "pdf"),
    "presentation": (_render_presentation_pptx, "pptx"),
    "plain":        (_render_plain_txt,         "txt"),
}


# ─── File-name derivation ────────────────────────────────────────────


_NAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(kind: DocKind, data: dict[str, Any], ext: str) -> str:
    raw = (
        data.get("title")
        or data.get("name")
        or f"{kind}"
    )
    cleaned = _NAME_SAFE_RE.sub("_", str(raw)).strip("._-")[:64]
    if not cleaned:
        cleaned = kind
    return f"{cleaned}.{ext}"


# ─── Entrypoint ──────────────────────────────────────────────────────


async def create(
    *,
    db: AsyncSession,
    user_id: UUID,
    kind: DocKind,
    prompt: str,
    title: str | None = None,
    role: str | None = None,
) -> GeneratedDoc:
    """Generate a document end-to-end: Sonnet → render → R2 → files row.

    Raises `GenerationError` with a user-safe message on failure.
    """
    if kind not in _RENDERERS:
        raise GenerationError(f"Unsupported document kind: {kind}")

    renderer, fmt = _RENDERERS[kind]
    mime = _FORMAT_TO_MIME[fmt]  # type: ignore[index]

    # 1) Structure the content with Sonnet 4.6. Higher quality than Haiku for
    #    prose generation; still well under a rupee per doc.
    brief = prompt.strip()
    if title:
        brief = f"Title: {title}\n\n{brief}"
    try:
        result = await complete(
            workload="quality",                         # Sonnet 4.6
            system=_system_prompt(kind, role),
            messages=[{"role": "user", "content": brief}],
            max_tokens=2048,
            temperature=0.3,                            # some creative freedom
        )
    except Exception as e:
        log.exception("doc_structure_failed", kind=kind)
        raise GenerationError(
            "Couldn't draft the document right now. Try again in a moment."
        ) from e

    data = _parse_json(result.content)

    # 2) Render bytes.
    try:
        content_bytes = renderer(data)
    except Exception as e:
        log.exception("doc_render_failed", kind=kind)
        raise GenerationError(
            "The document was drafted but rendering failed. "
            "Try a simpler version."
        ) from e

    name = _safe_name(kind, data, _FORMAT_TO_EXT[fmt])  # type: ignore[index]

    # 3) Upload + insert file row.
    r2_key, file_id = await r2_client.upload_bytes(
        user_id=user_id,
        folder="/documents",
        name=name,
        content=content_bytes,
        mime_type=mime,
    )
    db.add(FileRow(
        id=file_id,
        user_id=user_id,
        folder="/documents",
        name=name,
        mime_type=mime,
        size_bytes=len(content_bytes),
        r2_key=r2_key,
        source="generated",
    ))
    await db.flush()

    signed = await r2_client.signed_url(r2_key, expires_in=7 * 24 * 3600)  # 7-day link
    log.info(
        "doc_created",
        kind=kind, fmt=fmt, bytes=len(content_bytes),
        model=result.model, cost_paise=result.cost_inr_paise,
    )
    return GeneratedDoc(
        file_id=file_id,
        r2_key=r2_key,
        signed_url=signed,
        name=name,
        mime_type=mime,
        kind=kind,
        doc_format=fmt,                                 # type: ignore[arg-type]
        tokens_used=result.input_tokens + result.output_tokens,
        cost_inr_paise=result.cost_inr_paise,
    )
