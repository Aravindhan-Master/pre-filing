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
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "title", parent=styles["Heading1"], alignment=TA_CENTER, fontSize=13, spaceAfter=6
    )
    subtitle_style = ParagraphStyle(
        "subtitle", parent=styles["Normal"], alignment=TA_CENTER, fontSize=10, spaceAfter=12
    )
    cell_style = ParagraphStyle("cell", parent=styles["Normal"], fontSize=8, leading=10)

    story = []

    # Header
    story.append(Paragraph("INDEX", title_style))
    story.append(Paragraph(f"{paper_book['title']}", subtitle_style))
    story.append(Paragraph(f"Forum: {paper_book['forum']} | Type: {paper_book['application_type']}", subtitle_style))
    story.append(Spacer(1, 0.3 * cm))

    # Table headers
    col_headers = [
        Paragraph("<b>Sl.No</b>", cell_style),
        Paragraph("<b>Particulars of Document</b>", cell_style),
        Paragraph("<b>Part 1\n(Contents of Paper Book)\nPage No.</b>", cell_style),
        Paragraph("<b>Contents of File Alone\nPage No.</b>", cell_style),
        Paragraph("<b>Remarks</b>", cell_style),
    ]

    table_data = [col_headers]

    for row in index_rows:
        # Page range strings
        def fmt_range(start, end):
            if start is None:
                return ""
            if end and end != start:
                return f"{start} – {end}"
            return str(start)

        part1 = fmt_range(row.get("page_start_part1"), row.get("page_end_part1"))
        part2 = fmt_range(row.get("page_start_part2"), row.get("page_end_part2"))

        table_data.append([
            Paragraph(str(row.get("sl_no") or ""), cell_style),
            Paragraph(row.get("particulars", ""), cell_style),
            Paragraph(part1, cell_style),
            Paragraph(part2, cell_style),
            Paragraph(row.get("remarks") or "", cell_style),
        ])

    col_widths = [1.5 * cm, 9 * cm, 3 * cm, 3 * cm, 2.5 * cm]
    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F5F5F5")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F5F5")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))

    story.append(table)
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
