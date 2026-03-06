import io
from fastapi import APIRouter, Depends, Request
from app.schemas.requests import PaperBookUpdate
from core.config import config
from core.dependencies import AuthenticationRequired
from core.responseTypes import Success, NotFound
from core.supabase.client import get_supabase_client
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER


paperBooksRouter = APIRouter()


def build_index_pdf(paper_book: dict, index_rows: list) -> bytes:
    """Build the index page as a PDF using ReportLab."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()

    cell_style = ParagraphStyle(
        "cell",
        parent=styles["Normal"],
        fontSize=8,
        leading=11,
        wordWrap="CJK",
    )
    cell_bold_style = ParagraphStyle(
        "cell_bold",
        parent=styles["Normal"],
        fontSize=8,
        leading=11,
        fontName="Helvetica-Bold",
        wordWrap="CJK",
    )
    cell_center_style = ParagraphStyle(
        "cell_center",
        parent=styles["Normal"],
        fontSize=8,
        leading=11,
        alignment=TA_CENTER,
        wordWrap="CJK",
    )
    header_style = ParagraphStyle(
        "header",
        parent=styles["Normal"],
        fontSize=8,
        leading=11,
        fontName="Helvetica-Bold",
        alignment=TA_CENTER,
        wordWrap="CJK",
    )

    # ── Helper ───────────────────────────────────────────────────────────────
    def fmt_range(start, end):
        """Format page range as 'A1 — A2' or single value 'B'."""
        if start is None:
            return ""
        start_str = str(start)
        if end is not None and end != start:
            return f"{start_str} \u2014 {end}"  # em dash
        return start_str

    # ── Column widths ────────────────────────────────────────────────────────
    # Sl.No | Particulars | Part 1 | Part II | Remarks
    col_widths = [1.2 * cm, 9.5 * cm, 2.8 * cm, 2.8 * cm, 2.5 * cm]

    # ── Row 1: Merged header labels ──────────────────────────────────────────
    # ReportLab SPAN merges are defined in TableStyle.
    # We use empty strings for spanned cells.
    row1 = [
        Paragraph("Sl.no", header_style),
        Paragraph("Particulars of Document", header_style),
        Paragraph("Page No. of part to which it belongs", header_style),
        "",  # spanned by Part 1/Part II header above
        Paragraph("Remarks", header_style),
    ]

    # ── Row 2: Sub-headers for Part 1 and Part II ────────────────────────────
    row2 = [
        "",
        "",
        Paragraph("Part 1\n(Contents of\nPaper Book)", header_style),
        Paragraph("Part II\n(Contents of\nfile alone)", header_style),
        "",
    ]

    # ── Row 3: Roman numeral labels ──────────────────────────────────────────
    row3 = [
        Paragraph("i", cell_center_style),
        Paragraph("ii", cell_center_style),
        Paragraph("iii", cell_center_style),
        Paragraph("iv", cell_center_style),
        Paragraph("v", cell_center_style),
    ]

    table_data = [row1, row2, row3]

    # ── Data rows ────────────────────────────────────────────────────────────
    for row in index_rows:
        part1 = fmt_range(row.get("page_start_part1"), row.get("page_end_part1"))
        part2 = fmt_range(row.get("page_start_part2"), row.get("page_end_part2"))

        # Particulars: bold main title + optional remarks as sub-text
        particulars_text = f"<b>{row.get('particulars', '')}</b>"
        if row.get("remarks"):
            particulars_text += f"<br/><font size='7'>{row['remarks']}</font>"

        table_data.append([
            Paragraph(str(row.get("sl_no") or ""), cell_center_style),
            Paragraph(particulars_text, cell_style),
            Paragraph(part1, cell_center_style),
            Paragraph(part2, cell_center_style),
            Paragraph("", cell_style),  # remarks column left blank (already in particulars)
        ])

    # ── Build table ──────────────────────────────────────────────────────────
    table = Table(table_data, colWidths=col_widths, repeatRows=3)
    table.setStyle(TableStyle([
        # Grid for entire table
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),

        # Header background (rows 0 and 1)
        ("BACKGROUND", (0, 0), (-1, 1), colors.white),

        # Vertical alignment top for all
        ("VALIGN", (0, 0), (-1, -1), "TOP"),

        # Center align Sl.No column
        ("ALIGN", (0, 0), (0, -1), "CENTER"),

        # Center align Part 1 and Part II columns
        ("ALIGN", (2, 0), (3, -1), "CENTER"),

        # SPAN: "Page No. of part to which it belongs" spans col 2 and 3 in row 0
        ("SPAN", (2, 0), (3, 0)),

        # SPAN: Sl.no spans rows 0 and 1
        ("SPAN", (0, 0), (0, 1)),

        # SPAN: Particulars of Document spans rows 0 and 1
        ("SPAN", (1, 0), (1, 1)),

        # SPAN: Remarks spans rows 0 and 1
        ("SPAN", (4, 0), (4, 1)),

        # Padding
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),

        # Light grey background for roman numeral row
        ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#F5F5F5")),
    ]))

    story = [table]
    doc.build(story)
    return buf.getvalue()


async def merge_pdfs_with_bookmarks(
    index_pdf_bytes: bytes,
    document_paths_ordered: list,
    bookmarks: list,
    supabase,
) -> bytes:
    """
    Merge index PDF + all document PDFs, embed bookmarks.
    bookmarks: list of {title, page_number} — page_number is 1-based in the final merged PDF.
    The index page(s) are prepended, so all bookmark page numbers are offset by index page count.
    """
    writer = PdfWriter()

    # Add index pages
    index_reader = PdfReader(io.BytesIO(index_pdf_bytes))
    index_page_count = len(index_reader.pages)
    for page in index_reader.pages:
        writer.add_page(page)

    # Add document pages in order
    for storage_path in document_paths_ordered:
        try:
            pdf_bytes = await supabase.storage.from_(config.SUPABASE_PREFILING_STORAGE_BUCKET).download(storage_path)
            reader = PdfReader(io.BytesIO(pdf_bytes))
            for page in reader.pages:
                writer.add_page(page)
        except Exception:
            # Skip unreadable files gracefully
            continue

    # Add bookmarks (outline entries)
    # page_number in bookmarks is 1-based in the content (after index),
    # but pypdf uses 0-based page index in the full merged PDF.
    for bm in bookmarks:
        # bm["page_number"] is 1-based page in the document portion
        # offset by index pages to get position in full PDF
        page_idx = (bm["page_number"] - 1) + index_page_count
        total_pages = len(writer.pages)
        if 0 <= page_idx < total_pages:
            print(f"Adding bookmark '{bm['title']}' at page {page_idx + 1} of {total_pages}")
            writer.add_outline_item(title=bm["title"], page_number=page_idx)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


async def build_final_pdf(paper_book_id: str, user_id: str, supabase) -> bytes:
    paper_book = (
        await supabase.table("paper_books")
        .select("*")
        .eq("id", paper_book_id)
        .eq("user_id", user_id)
        .single()
        .execute()
    )
    if not paper_book.data:
        raise NotFound(message="Paper book not found")

    # Fetch index rows
    index_res = (
        await supabase.table("paper_book_index_rows")
        .select("*")
        .eq("paper_book_id", paper_book_id)
        .order("order_index")
        .execute()
    )
    index_rows = index_res.data or []

    if not index_rows:
        raise NotFound(message="No index rows found. Please generate the index first.")

    # Fetch documents ordered by section order + document order
    sections_res = (
        await supabase.table("paper_book_sections")
        .select("id, order_index")
        .eq("paper_book_id", paper_book_id)
        .order("order_index")
        .execute()
    )
    section_order = {s["id"]: s["order_index"] for s in (sections_res.data or [])}

    docs_res = (
        await supabase.table("paper_book_documents")
        .select("doc_id, section_id, order_index")
        .eq("paper_book_id", paper_book_id)
        .execute()
    )
    docs = docs_res.data or []

    # Sort: first by section order, then by document order within section
    docs_sorted = sorted(
        docs,
        key=lambda d: (
            section_order.get(d.get("section_id"), 9999),
            d.get("order_index", 0)
        )
    )

    doc_ids = [d["doc_id"] for d in docs_sorted]

    # Fetch storage paths with id included
    storage_paths_res = (
        await supabase.table("paper_book_files")
        .select("id, storage_path")
        .in_("id", doc_ids)
        .execute()
    )

    storage_paths_data = storage_paths_res.data or []

    # Create mapping: id -> storage_path
    storage_path_map = {
        row["id"]: row["storage_path"]
        for row in storage_paths_data
    }

    # Rebuild list in sorted order
    storage_paths = [
        storage_path_map.get(doc_id)
        for doc_id in doc_ids
        if doc_id in storage_path_map
    ]

    # Fetch bookmarks
    bookmarks_res = (
        await supabase.table("paper_book_bookmarks")
        .select("title, page_number")
        .eq("paper_book_id", paper_book_id)
        .order("order_index")
        .execute()
    )
    bookmarks = bookmarks_res.data or []

    # Build index PDF
    index_pdf = build_index_pdf(paper_book.data, index_rows)

    # Merge everything
    final_pdf = await merge_pdfs_with_bookmarks(index_pdf, storage_paths, bookmarks, supabase)

    # Update status
    await supabase.from_("paper_books").update({"status": "completed"}).eq("id", paper_book_id).execute()

    return final_pdf



@paperBooksRouter.get("/", dependencies=[Depends(AuthenticationRequired)])
async def get_paper_book(
    paper_book_id: str,
    request: Request,
):
    supabase = await get_supabase_client(request.state.token)
    res = (
        await supabase.table("paper_books")
        .select("*")
        .eq("id", paper_book_id)
        .execute()
    )
    if not res.data:
        raise NotFound(message="Paper book not found")

    response = {"paper_book": res.data}
    return Success(data=response, message="Paper book retrieved successfully")


@paperBooksRouter.patch("/", dependencies=[Depends(AuthenticationRequired)])
async def update_paper_book(
    paper_book_id: str,
    payload: PaperBookUpdate,
    request: Request,
):
    supabase = await get_supabase_client(request.state.token)
    update_data = payload.model_dump(exclude_none=True)
    res = (
        await supabase.table("paper_books")
        .update(update_data)
        .eq("id", paper_book_id)
        .eq("user_id", request.state.sub)
        .execute()
    )
    response = {"paper_book": res.data}
    return Success(data=response, message="Paper book updated successfully")


@paperBooksRouter.delete("/", dependencies=[Depends(AuthenticationRequired)])
async def delete_paper_book(
    paper_book_id: str,
    request: Request,
):
    supabase = await get_supabase_client(request.state.token)
    res = (
        await supabase.table("paper_books")
        .update({"deleted_at": "now()"})
        .eq("id", paper_book_id)
        .eq("user_id", request.state.sub)
        .execute()
    )
    response = {"paper_book": res.data}
    return Success(data=response, message="Paper book deleted successfully")


@paperBooksRouter.get("/export/", dependencies=[Depends(AuthenticationRequired)])
async def preview_pdf(
    request: Request,
    paper_book_id: str,
):
    """Stream the merged PDF for in-browser preview (inline)."""
    supabase = await get_supabase_client(request.state.token)
    final_pdf = await build_final_pdf(paper_book_id, request.state.sub, supabase)

    file_path = f"{request.state.sub}/{paper_book_id}/final.pdf"

    await supabase.storage.from_(config.SUPABASE_PREFILING_STORAGE_BUCKET).upload(
        file_path,
        final_pdf,
        file_options={"content-type": "application/pdf", "upsert": "true"},
    )

    signed_url = await supabase.storage.from_(
        config.SUPABASE_PREFILING_STORAGE_BUCKET
    ).create_signed_url(file_path, 3600)  # valid for 1 hour

    return Success(data={"url": signed_url["signedURL"]}, message="PDF generated successfully")
