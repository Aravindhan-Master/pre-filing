from fastapi import APIRouter, Depends, Request
from app.schemas.requests import (
    IndexRowCreate, IndexRowUpdate, IndexReorder,
)
from core.dependencies import AuthenticationRequired
from core.supabase.client import get_supabase_client
from core.responseTypes import Success, NotFound


indexRowsRouter = APIRouter()


def compute_page_numbers(sections: list, docs_by_section: dict) -> list:
    """
    Compute running page numbers for Part 1 and Part 2.
    - Part 1 counter: increments for sections with page_number_column in (part1, both)
    - Part 2 counter: increments for sections with page_number_column in (part2, both)
    Page count comes from the documents' actual page counts (sum per section).
    If no documents in a section, page numbers are left None.
    """
    rows = []
    part_cursor = 1

    for order_idx, section in enumerate(sections):
        section_id = section["id"]
        col = section["page_number_column"]  # part1 | part2 | both

        docs = docs_by_section.get(section_id, [])
        # Sum page counts (None page_count treated as 0, so pages remain None)
        total_pages = None
        if docs:
            counts = [d.get("paper_book_files", {}).get("page_count") for d in docs]
            if any(c is not None for c in counts):
                total_pages = sum(c or 0 for c in counts)

        row = {
            "section_id": section_id,
            "particulars": section["name"],
            "order_index": order_idx + 1,
            "sl_no": str(order_idx + 1),
            "is_custom": False,
            "is_edited": False,
            "page_start_part1": None,
            "page_end_part1": None,
            "page_start_part2": None,
            "page_end_part2": None,
        }

        if total_pages is not None and total_pages > 0:
            if col == "both":
                row["page_start_part1"] = part_cursor
                row["page_end_part1"] = part_cursor + total_pages - 1
                row["page_start_part2"] = part_cursor
                row["page_end_part2"] = part_cursor + total_pages - 1
                part_cursor += total_pages

            elif col == "part1":
                row["page_start_part1"] = part_cursor
                row["page_end_part1"] = part_cursor + total_pages - 1
                part_cursor += total_pages

            elif col == "part2":
                row["page_start_part2"] = part_cursor
                row["page_end_part2"] = part_cursor + total_pages - 1
                part_cursor += total_pages
        else:
            # Section has no docs or no page counts: advance cursor only if col matches
            # Leave page numbers as None (blank in index)
            pass

        rows.append(row)

    return rows


@indexRowsRouter.post("/generate/", dependencies=[Depends(AuthenticationRequired)])
async def generate_index(
    request: Request,
    paper_book_id: str,
):
    """
    Auto-generate index rows from sections and their documents.
    Deletes existing non-custom rows and regenerates.
    Custom rows are preserved.
    """
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

    # Fetch sections ordered
    sections_res = (
        await supabase.table("paper_book_sections")
        .select("*")
        .eq("paper_book_id", paper_book_id)
        .order("order_index")
        .execute()
    )
    sections = sections_res.data or []

    # Fetch all documents
    docs_res = (
        await supabase.table("paper_book_documents")
        .select("*, paper_book_files(page_count)")
        .eq("paper_book_id", paper_book_id)
        .order("order_index")
        .execute()
    )
    docs = docs_res.data or []

    # Group docs by section
    docs_by_section: dict = {}
    for doc in docs:
        sid = doc.get("section_id")
        if sid:
            docs_by_section.setdefault(sid, []).append(doc)

    # Delete existing non-custom rows
    await supabase.table("paper_book_index_rows").delete().eq(
        "paper_book_id", paper_book_id
    ).eq("is_custom", False).execute()

    # Compute page numbers
    rows = compute_page_numbers(sections, docs_by_section)

    if not rows:
        return []

    # Insert rows
    insert_payload = [{**row, "paper_book_id": paper_book_id} for row in rows]
    res = await supabase.table("paper_book_index_rows").insert(insert_payload).execute()

    # Update paper book status
    await supabase.table("paper_books").update({"status": "index_created"}).eq(
        "id", paper_book_id
    ).execute()

    response = {"index_rows": res.data}
    return Success(data=response, message="Index generated successfully")


@indexRowsRouter.get("/", dependencies=[Depends(AuthenticationRequired)])
async def get_index(
    request: Request,
    paper_book_id: str,
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
        await supabase.table("paper_book_index_rows")
        .select("*, paper_book_sections(id, paper_book_documents(id, paper_book_files(*)))")
        .eq("paper_book_id", paper_book_id)
        .order("order_index")
        .execute()
    )
    response = {"index_rows": res.data or []}
    return Success(data=response, message="Index rows fetched successfully")


@indexRowsRouter.post("/rows/", dependencies=[Depends(AuthenticationRequired)])
async def create_index_row(
    request: Request,
    paper_book_id: str,
    payload: IndexRowCreate,
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

    if payload.order_index is not None:
        order_index = payload.order_index
    else:
        res = (
            await supabase.table("paper_book_index_rows")
            .select("order_index")
            .eq("paper_book_id", paper_book_id)
            .order("order_index", desc=True)
            .limit(1)
            .execute()
        )
        max_order = res.data[0]["order_index"] if res.data else 0
        order_index = max_order + 1

    insert_data = {
        "paper_book_id": paper_book_id,
        "section_id": payload.section_id,
        "sl_no": payload.sl_no,
        "particulars": payload.particulars,
        "page_start_part1": payload.page_start_part1,
        "page_end_part1": payload.page_end_part1,
        "page_start_part2": payload.page_start_part2,
        "page_end_part2": payload.page_end_part2,
        "remarks": payload.remarks,
        "order_index": order_index,
        "is_custom": True,
        "is_edited": False,
    }

    res = await supabase.table("paper_book_index_rows").insert(insert_data).execute()
    response = {"index_row": res.data}
    return Success(data=response, message="Index row created successfully")


@indexRowsRouter.patch("/rows/{row_id}/", dependencies=[Depends(AuthenticationRequired)])
async def update_index_row(
    request: Request,
    paper_book_id: str,
    row_id: str,
    payload: IndexRowUpdate,
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
        await supabase.table("paper_book_index_rows")
        .select("*")
        .eq("id", row_id)
        .eq("paper_book_id", paper_book_id)
        .single()
        .execute()
    )
    if not res.data:
        raise NotFound(message="Index row not found")

    update_data = payload.model_dump()

    # Mark as manually edited
    update_data["is_edited"] = True

    res = (
        await supabase.table("paper_book_index_rows")
        .update(update_data)
        .eq("id", row_id)
        .eq("paper_book_id", paper_book_id)
        .execute()
    )
    response = {"index_row": res.data}
    return Success(data=response, message="Index row updated successfully")


@indexRowsRouter.delete("/rows/{row_id}/", dependencies=[Depends(AuthenticationRequired)])
async def delete_index_row(
    request: Request,
    paper_book_id: str,
    row_id: str,
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
        await supabase.table("paper_book_index_rows")
        .select("*")
        .eq("id", row_id)
        .eq("paper_book_id", paper_book_id)
        .single()
        .execute()
    )
    if not res.data:
        raise NotFound(message="Index row not found")

    await supabase.table("paper_book_index_rows").delete().eq("id", row_id).execute()

    response = {}
    return Success(data=response, message="Index row deleted successfully")


@indexRowsRouter.patch("/reorder/", dependencies=[Depends(AuthenticationRequired)])
async def reorder_index(
    request: Request,
    paper_book_id: str,
    payload: IndexReorder,
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

    updated = []
    for idx, row_id in enumerate(payload.ordered_ids):
        res = (
            await supabase.table("paper_book_index_rows")
            .update({"order_index": idx + 1})
            .eq("id", row_id)
            .eq("paper_book_id", paper_book_id)
            .execute()
        )
        if res.data:
            updated.append(res.data[0])

    response = {"index_rows": updated}
    return Success(data=response, message="Index rows reordered successfully")
