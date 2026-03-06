import io
import os
from io import BytesIO
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from pypdf import PdfReader, PdfWriter
from app.constants import VALID_FILE_FORMATS
from app.utils import normalize_supabase_storage_key, encode_url_path, remove_timestamp_from_storage_filename
from app.schemas.requests import (
    DocumentCreate, DocumentUpdate, DocumentAssignSection, DeletePagesRequest,
    DocumentReorder, DocumentSplitRequest, CommitDocumentUpload,
)
from core.config import config
from core.dependencies import AuthenticationRequired
from core.logging import logger
from core.supabase.client import get_supabase_client
from core.responseTypes import Success, NotFound, BadRequest


router = APIRouter()


async def upload_pdf_to_storage(supabase, storage_path: str, pdf_bytes: bytes) -> str:
    """Upload PDF bytes to Supabase storage, return the path."""
    try:
        await supabase.storage.from_(config.SUPABASE_PREFILING_STORAGE_BUCKET).upload(
            path=storage_path,
            file=pdf_bytes,
            file_options={"content-type": "application/pdf", "upsert": "true"},
        )
        return storage_path
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload file to storage: {str(e)}")


async def delete_from_storage(supabase, storage_path: str):
    try:
        await supabase.storage.from_(config.SUPABASE_PREFILING_STORAGE_BUCKET).remove([storage_path])
    except Exception:
        pass


@router.post("/", dependencies=[Depends(AuthenticationRequired)])
async def create_document_record(
    request: Request,
    paper_book_id: str,
    payload: DocumentCreate,
):
    """
    FE uploads file directly to Supabase storage,
    then calls this endpoint with the doc_id to create the DB record.
    """
    supabase = await get_supabase_client(request.state.token)
    res = (
        await supabase.table("paper_books")
        .select("id")
        .eq("id", paper_book_id)
        .eq("user_id", request.state.sub)
        .execute()
    )
    if not res.data:
        raise NotFound(message="Paper book not found")

    # Determine order_index within section (or global)
    if payload.order_index is not None:
        order_index = payload.order_index
    else:
        query = (
            supabase.table("paper_book_documents")
            .select("order_index")
            .eq("paper_book_id", paper_book_id)
        )
        if payload.section_id:
            query = query.eq("section_id", payload.section_id)

        res = await query.order("order_index", desc=True).limit(1).execute()
        max_order = res.data[0]["order_index"] if res.data else 0
        order_index = max_order + 1

    insert_data = {
        "paper_book_id": paper_book_id,
        "doc_id": payload.doc_id,
        "section_id": payload.section_id,
        "order_index": order_index,
        "is_split_child": False,
    }

    res = await supabase.table("paper_book_documents").insert(insert_data).execute()

    # Update paper book status if still draft
    await supabase.table("paper_books").update({"status": "documents_uploaded"}).eq(
        "id", paper_book_id
    ).eq("status", "draft").execute()

    response = {"document": res.data}
    return Success(data=response, message="Document record created successfully")


@router.get("/", dependencies=[Depends(AuthenticationRequired)])
async def list_documents(
    request: Request,
    paper_book_id: str,
):
    supabase = await get_supabase_client(request.state.token)

    res = (
        await supabase.table("paper_books")
        .select("id")
        .eq("id", paper_book_id)
        .eq("user_id", request.state.sub)
        .execute()
    )
    if not res.data:
        raise NotFound(message="Paper book not found")

    # fetch only the documents that are not assigned to any section
    res = (
        await supabase.table("paper_book_documents")
        .select("*, paper_book_files(*)")
        .eq("paper_book_id", paper_book_id)
        .is_("section_id", r"null")
        .order("order_index")
        .execute()
    )

    response = {"documents": res.data or []}
    return Success(data=response, message="Documents retrieved successfully")


@router.get("/upload/", dependencies=[Depends(AuthenticationRequired)])
async def get_upload_url(
    request: Request,
    paper_book_id: str,
    filename: str,
):
    supabase = await get_supabase_client(request.state.token)
    res = (
        await supabase.table("paper_books")
        .select("id")
        .eq("id", paper_book_id)
        .eq("user_id", request.state.sub)
        .execute()
    )
    if not res.data:
        raise NotFound(message="Paper book not found")

    if "." not in filename:
        return BadRequest(message="File name must include an extension", error_code="missing_file_extension")

    extension = filename.split(".")[-1].lower()
    if extension not in VALID_FILE_FORMATS:
        return BadRequest(message="Invalid file format", error_code="invalid_file_format")

    title = ".".join(filename.split(".")[:-1])
    title = normalize_supabase_storage_key(title)
    date_time = (
        datetime.now(timezone.utc)
        .isoformat()
        .replace(":", "-")
        .replace("+", "-")
        .replace(".", "-")
    )


    # Generate a unique storage path for the new document
    file_path = f"{title}_{date_time}.{extension}"
    file_path = f"{request.state.sub}/{paper_book_id}/{file_path}"
    storage_path = f"paper-books/{file_path}"

    # Generate signed upload URL (valid for 1 hour = 3600 seconds)
    bucket = supabase.storage.from_(config.SUPABASE_PREFILING_STORAGE_BUCKET)
    upload_url_response = await bucket.create_signed_upload_url(
        path=file_path,
    )

    if not upload_url_response or not upload_url_response.get("signedUrl"):
        logger.error(
            f"Failed to generate signed upload URL: {upload_url_response}"
        )
        raise HTTPException(status_code=500, detail="Failed to generate upload URL")
    
    signed_url = upload_url_response.get("signedUrl")
    upload_token = upload_url_response.get("token")

    # Encode the URL path to handle special characters
    encoded_signed_url = encode_url_path(signed_url)

    response = {
        "upload_url": encoded_signed_url,
        "file_path": storage_path,
        "upload_token": upload_token,
        "paper_book_id": paper_book_id,
    }

    return Success(data=response, message="Upload URL generated successfully")


@router.post("/upload/", dependencies=[Depends(AuthenticationRequired)])
async def commit_uploaded_document(
    request: Request,
    paper_book_id: str,
    payload: CommitDocumentUpload,
):
    """
    After FE uploads file to Supabase storage using the signed URL,
    it calls this endpoint to create the DB record for the uploaded document.
    """
    supabase = await get_supabase_client(request.state.token)

    file_name = remove_timestamp_from_storage_filename(
        payload.file_path.split("/")[-1]
    )

    file_path = payload.file_path

    # Remove bucket prefix if present (e.g., 'paper-books/')
    if payload.file_path.startswith(
        f"{config.SUPABASE_PREFILING_STORAGE_BUCKET}/"
    ):
        file_path = payload.file_path[
            len(f"{config.SUPABASE_PREFILING_STORAGE_BUCKET}/") :
        ]

    bucket = supabase.storage.from_(
        config.SUPABASE_PREFILING_STORAGE_BUCKET
    )

    file_exists = await bucket.exists(path=file_path)
    if not file_exists:
        return NotFound(message="Uploaded file not found in storage")

    file_response = await bucket.download(path=file_path)
    if not file_response:
        return NotFound(
            message="Failed to retrieve uploaded file from storage"
        )

    title = file_name
    uploaded_filename = file_path.split("/")[-1]

    page_count = None
    if uploaded_filename.lower().endswith(".pdf"):
        try:
            pdf_reader = PdfReader(BytesIO(file_response))
            page_count = len(pdf_reader.pages)
        except Exception:
            page_count = None

    # Insert document record into database
    document_data = {
        "user_id": request.state.sub,
        "title": title,
        "uploaded_filename": uploaded_filename,
        "storage_path": file_path,
        "file_size": len(file_response),
        "page_count": page_count,
    }

    doc_insert_response = (
        await supabase.table("paper_book_files")
        .insert(document_data)
        .execute()
    )

    paper_book_doc_response = (
        await supabase.table("paper_book_documents")
        .insert(
            {
                "paper_book_id": paper_book_id,
                "doc_id": doc_insert_response.data[0]["id"],
                "is_split_child": False,
            }
        )
        .execute()
    )

    await (
        supabase.table("paper_books")
        .update({"status": "documents_uploaded"})
        .eq("id", paper_book_id)
        .eq("status", "draft")
        .execute()
    )

    return Success(
        data={"document": paper_book_doc_response.data},
        message="Uploaded document committed successfully",
    )

@router.patch("/reorder/", dependencies=[Depends(AuthenticationRequired)])
async def reorder_documents(
    request: Request,
    paper_book_id: str,
    payload: DocumentReorder,
):
    supabase = await get_supabase_client(request.state.token)
    res = (
        await supabase.table("paper_books")
        .select("id")
        .eq("id", paper_book_id)
        .eq("user_id", request.state.sub)
        .execute()
    )
    if not res.data:
        raise NotFound(message="Paper book not found")

    for item in payload.items:
        update_data = {"order_index": item.order_index}
        if item.section_id is not None:
            update_data["section_id"] = item.section_id

        await supabase.table("paper_book_documents").update(update_data).eq(
            "id", item.id
        ).eq("paper_book_id", paper_book_id).execute()

    response = {}
    return Success(data=response, message="Documents reordered successfully")


@router.patch("/{doc_id}/", dependencies=[Depends(AuthenticationRequired)])
async def update_document(
    request: Request,
    paper_book_id: str,
    doc_id: str,
    payload: DocumentUpdate,
):
    supabase = await get_supabase_client(request.state.token)
    res = (
        await supabase.table("paper_books")
        .select("id")
        .eq("id", paper_book_id)
        .eq("user_id", request.state.sub)
        .execute()
    )
    if not res.data:
        raise NotFound(message="Paper book not found")

    update_data = payload.model_dump(exclude_none=True)

    res = (
        await supabase.table("paper_book_files")
        .update(update_data)
        .eq("id", doc_id)
        .execute()
    )
    response = {"document": res.data}
    return Success(data=response, message="Document updated successfully")


@router.delete("/{doc_id}/", dependencies=[Depends(AuthenticationRequired)])
async def delete_document(
    request: Request,
    paper_book_id: str,
    doc_id: str,
):
    supabase = await get_supabase_client(request.state.token)
    res = (
        await supabase.table("paper_books")
        .select("id")
        .eq("id", paper_book_id)
        .eq("user_id", request.state.sub)
        .execute()
    )
    if not res.data:
        raise NotFound(message="Paper book not found")

    res = (
        await supabase.table("paper_book_files")
        .select("*")
        .eq("id", doc_id)
        .execute()
    )
    if not res.data:
        raise NotFound(message="Document not found")

    doc = res.data[0]

    # Delete from storage
    await delete_from_storage(supabase, doc["storage_path"])

    await supabase.table("paper_book_files").delete().eq("id", doc_id).execute()

    response = {}
    return Success(data=response, message="Document deleted successfully")



@router.patch("/{doc_id}/assign-section/", dependencies=[Depends(AuthenticationRequired)])
async def assign_section(
    request: Request,
    paper_book_id: str,
    doc_id: str,
    payload: DocumentAssignSection,
):
    supabase = await get_supabase_client(request.state.token)

    res = (
        await supabase.table("paper_books")
        .select("id")
        .eq("id", paper_book_id)
        .eq("user_id", request.state.sub)
        .single()
        .execute()
    )
    if not res.data:
        raise NotFound(message="Paper book not found")

    res = (
        await supabase.table("paper_book_documents")
        .select("*")
        .eq("id", doc_id)
        .eq("paper_book_id", paper_book_id)
        .single()
        .execute()
    )
    if not res.data:
        raise NotFound(message="Document not found")

    # Determine order within new section
    if payload.order_index is not None:
        order_index = payload.order_index
    else:
        res = (
            await supabase.table("paper_book_documents")
            .select("order_index")
            .eq("paper_book_id", paper_book_id)
            .eq("section_id", payload.section_id)
            .order("order_index", desc=True)
            .limit(1)
            .execute()
        )
        max_order = res.data[0]["order_index"] if res.data else 0
        order_index = max_order + 1

    res = (
        await supabase.table("paper_book_documents")
        .update({"section_id": payload.section_id, "order_index": order_index})
        .eq("id", doc_id)
        .eq("paper_book_id", paper_book_id)
        .execute()
    )

    response = {"document": res.data}
    return Success(data=response, message="Document assigned to section successfully")


@router.patch("/{doc_id}/remove-section/", dependencies=[Depends(AuthenticationRequired)])
async def remove_section(
    request: Request,
    paper_book_id: str,
    doc_id: str,
):
    supabase = await get_supabase_client(request.state.token)

    # Verify paper book ownership
    res = (
        await supabase.table("paper_books")
        .select("id")
        .eq("id", paper_book_id)
        .eq("user_id", request.state.sub)
        .single()
        .execute()
    )
    if not res.data:
        raise NotFound(message="Paper book not found")

    # Verify document exists
    res = (
        await supabase.table("paper_book_documents")
        .select("*")
        .eq("id", doc_id)
        .eq("paper_book_id", paper_book_id)
        .single()
        .execute()
    )
    if not res.data:
        raise NotFound(message="Document not found")

    # Remove section assignment
    res = (
        await supabase.table("paper_book_documents")
        .update({
            "section_id": None,
            "order_index": None
        })
        .eq("id", doc_id)
        .eq("paper_book_id", paper_book_id)
        .execute()
    )

    response = {"document": res.data}
    return Success(data=response, message="Document removed from section successfully")


@router.post("/{doc_id}/split/", dependencies=[Depends(AuthenticationRequired)])
async def split_document(
    request: Request,
    paper_book_id: str,
    doc_id: str,
    payload: DocumentSplitRequest,
):
    """
    Split a PDF into multiple parts based on page ranges.
    The original document is replaced by the split parts.
    Pages not included in any range are discarded.
    """
    supabase = await get_supabase_client(request.state.token)

    # Verify paper book ownership
    pb_res = (
        await supabase.table("paper_books")
        .select("id")
        .eq("id", paper_book_id)
        .eq("user_id", request.state.sub)
        .execute()
    )
    if not pb_res.data:
        raise NotFound(message="Paper book not found")

    # Fetch paper_book_document record
    pbd_res = (
        await supabase.table("paper_book_documents")
        .select("*")
        .eq("doc_id", doc_id)
        .eq("paper_book_id", paper_book_id)
        .execute()
    )
    if not pbd_res.data:
        raise NotFound(message="Document not found")

    pbd = pbd_res.data[0]  # paper_book_documents row

    # Fetch the actual document from paper_book_files table using doc_id FK
    doc_res = (
        await supabase.table("paper_book_files")
        .select("id, storage_path, uploaded_filename, file_size")
        .eq("id", pbd["doc_id"])
        .execute()
    )
    if not doc_res.data:
        raise NotFound(message="Source document record not found")

    original_doc = doc_res.data[0]  # paper_book_files row

    # Validate ranges
    for r in payload.ranges:
        if r.end < r.start:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid range: end ({r.end}) must be >= start ({r.start})",
            )

    # Download original PDF from storage
    pdf_bytes = await supabase.storage.from_(config.SUPABASE_PREFILING_STORAGE_BUCKET).download(original_doc["storage_path"])

    reader = PdfReader(io.BytesIO(pdf_bytes))
    total_pages = len(reader.pages)

    # Validate page numbers against actual page count
    for r in payload.ranges:
        if r.start > total_pages or r.end > total_pages:
            raise HTTPException(
                status_code=400,
                detail=f"Page range {r.start}-{r.end} exceeds document page count ({total_pages})",
            )

    # Derive storage dir and stem from original doc
    original_dir = os.path.dirname(original_doc["storage_path"])
    original_stem = os.path.splitext(os.path.basename(original_doc["storage_path"]))[0]

    created_docs = []

    for idx, r in enumerate(payload.ranges):
        # Build split PDF bytes
        writer = PdfWriter()
        for page_num in range(r.start - 1, r.end):  # convert to 0-based
            writer.add_page(reader.pages[page_num])

        buf = io.BytesIO()
        writer.write(buf)
        split_bytes = buf.getvalue()

        # Determine filename for this part
        if r.filename:
            part_filename = r.filename if r.filename.endswith(".pdf") else f"{r.filename}.pdf"
        else:
            part_filename = f"{original_stem}_part{idx + 1}.pdf"

        # Build storage path
        storage_path = f"{original_dir}/{part_filename}" if original_dir else part_filename
        storage_path = storage_path.replace("//", "/")

        # Upload split PDF to storage
        await upload_pdf_to_storage(supabase, storage_path, split_bytes)

        # Create new record in `paper_book_files` table
        new_doc_res = (
            await supabase.table("paper_book_files")
            .insert({
                "user_id": request.state.sub,
                "title": part_filename,
                "storage_path": storage_path,
                "uploaded_filename": part_filename,
                "file_size": len(split_bytes),
                "page_count": r.end - r.start + 1
            })
            .execute()
        )
        new_doc = new_doc_res.data[0]  # has the new doc_id

        # Create new record in `paper_book_documents` table
        new_pbd_res = (
            await supabase.table("paper_book_documents")
            .insert({
                "paper_book_id": paper_book_id,
                "section_id": pbd["section_id"],
                "doc_id": new_doc["id"],
                "order_index": pbd["order_index"] + idx,
                "is_split_child": True,
                "parent_document_id": pbd["id"],
                "split_page_start": r.start,
                "split_page_end": r.end,
            })
            .execute()
        )

        created_docs.append({
            **new_pbd_res.data[0],
            "storage_path": storage_path,
            "uploaded_filename": part_filename,
            "file_size": len(split_bytes),
            "page_count": r.end - r.start + 1,
        })

    # Delete original from storage
    await delete_from_storage(supabase, original_doc["storage_path"])

    # Delete original record from `paper_book_files` table
    await supabase.table("paper_book_files").delete().eq("id", original_doc["id"]).execute()

    # Delete original record from `paper_book_documents` table
    await supabase.table("paper_book_documents").delete().eq("id", pbd["id"]).execute()

    return Success(data={"created_documents": created_docs}, message="Document split successfully")


@router.get("/{doc_id}/url/", dependencies=[Depends(AuthenticationRequired)])
async def get_document_download_url(
    request: Request,
    paper_book_id: str,
    doc_id: str,
):
    supabase = await get_supabase_client(request.state.token)
    res = (
        await supabase.table("paper_books")
        .select("id")
        .eq("id", paper_book_id)
        .eq("user_id", request.state.sub)
        .execute()
    )
    if not res.data:
        raise NotFound(message="Paper book not found")

    doc_res = (
        await supabase.table("paper_book_files")
        .select("storage_path")
        .eq("id", doc_id)
        .execute()
    )
    if not doc_res.data:
        raise NotFound(message="Document not found")

    storage_path = doc_res.data[0]["storage_path"]

    download_url_response = await supabase.storage.from_(config.SUPABASE_PREFILING_STORAGE_BUCKET).create_signed_url(
        path=storage_path,
        expires_in=3600,
    )

    if not download_url_response or not download_url_response.get("signedUrl"):
        logger.error(
            f"Failed to generate signed download URL: {download_url_response}"
        )
        raise HTTPException(status_code=500, detail="Failed to generate download URL")

    signed_download_url = download_url_response.get("signedUrl")

    return Success(data={"download_url": signed_download_url}, message="Download URL generated successfully")

@router.post("/{doc_id}/delete-pages/", dependencies=[Depends(AuthenticationRequired)])
async def delete_pages(
    request: Request,
    paper_book_id: str,
    doc_id: str,
    payload: DeletePagesRequest,
):
    """
    Delete specific pages from a document PDF.
    - page_indices are 1-based
    - Original file in storage is overwritten with the new PDF
    - Update page_count in paper_book_files table
    """
    supabase = await get_supabase_client(request.state.token)

    # Verify paper book ownership
    pb_res = (
        await supabase.table("paper_books")
        .select("id")
        .eq("id", paper_book_id)
        .eq("user_id", request.state.sub)
        .single()
        .execute()
    )
    if not pb_res.data:
        raise NotFound(message="Paper book not found")

    # Fetch actual document from paper_book_files table
    doc_res = (
        await supabase.table("paper_book_files")
        .select("id, storage_path, uploaded_filename, file_size")
        .eq("id", doc_id)
        .execute()
    )
    if not doc_res.data:
        raise NotFound(message="Source document record not found")

    original_doc = doc_res.data[0]

    # Download PDF from storage
    pdf_bytes = await supabase.storage.from_(
        config.SUPABASE_PREFILING_STORAGE_BUCKET
    ).download(original_doc["storage_path"])

    reader = PdfReader(io.BytesIO(pdf_bytes))
    total_pages = len(reader.pages)

    # Validate page indices
    invalid = [p for p in payload.page_indices if p < 1 or p > total_pages]
    if invalid:
        raise BadRequest(
            message=f"Invalid page indices {invalid}. Document has {total_pages} pages (1-based).",
            error_code="invalid_page_indices",
        )

    # Check that we're not deleting all pages
    pages_to_delete = set(payload.page_indices)
    if len(pages_to_delete) >= total_pages:
        raise BadRequest(
            message="Cannot delete all pages from a document.",
            error_code="cannot_delete_all_pages",
        )

    # Build new PDF excluding deleted pages
    writer = PdfWriter()
    for page_num in range(total_pages):
        if (page_num + 1) not in pages_to_delete:  # convert to 1-based for comparison
            writer.add_page(reader.pages[page_num])

    buf = io.BytesIO()
    writer.write(buf)
    new_pdf_bytes = buf.getvalue()
    remaining_pages = len(writer.pages)

    # Overwrite the same file in storage
    await supabase.storage.from_(config.SUPABASE_PREFILING_STORAGE_BUCKET).upload(
        original_doc["storage_path"],
        new_pdf_bytes,
        file_options={"content-type": "application/pdf", "upsert": "true"},
    )

    # Update file_size and page_count in paper_book_files table
    await supabase.table("paper_book_files").update(
        {"file_size": len(new_pdf_bytes), "page_count": remaining_pages}
    ).eq("id", original_doc["id"]).execute()

    return Success(
        data={
            "doc_id": doc_id,
            "pages_deleted": sorted(pages_to_delete),
            "remaining_pages": remaining_pages,
        },
        message="Pages deleted successfully",
    )
