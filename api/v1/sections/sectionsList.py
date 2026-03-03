from fastapi import APIRouter, Depends, Request
from typing import List
from app.schemas.requests import SectionCreate, SectionReorder
from core.dependencies import AuthenticationRequired
from core.supabase.client import get_supabase_client
from core.responseTypes import Success, NotFound

sectionsListRouter = APIRouter()


@sectionsListRouter.get("/", dependencies=[Depends(AuthenticationRequired)])
async def list_sections(
    request: Request,
    paper_book_id: str,
):
    supabase = await get_supabase_client(request.state.token)
    paper_books = (
        await supabase.table("paper_books")
        .select("id")
        .eq("id", paper_book_id)
        .eq("user_id", request.state.sub)
        .single()
        .execute()
    )
    if not paper_books.data:
        raise NotFound(message="Paper book not found")

    res = (
        await supabase.table("paper_book_sections")
        .select("*")
        .eq("paper_book_id", paper_book_id)
        .order("order_index")
        .execute()
    )
    response = {"sections": res.data or []}
    return Success(data=response, message="Sections retrieved successfully")


@sectionsListRouter.post("/", dependencies=[Depends(AuthenticationRequired)])
async def create_section(
    request: Request,
    paper_book_id: str,
    payload: SectionCreate,
):
    supabase = await get_supabase_client(request.state.token)
    paper_books = (
        await supabase.table("paper_books")
        .select("id")
        .eq("id", paper_book_id)
        .eq("user_id", request.state.sub)
        .single()
        .execute()
    )
    if not paper_books.data:
        raise NotFound(message="Paper book not found")

    # Determine order_index: append at end if not provided
    if payload.order_index is not None:
        order_index = payload.order_index
    else:
        res = (
            await supabase.table("paper_book_sections")
            .select("order_index")
            .eq("paper_book_id", paper_book_id)
            .order("order_index", desc=True)
            .limit(1)
            .execute()
        )
        max_order = res.data[0]["order_index"] if res.data else 0
        order_index = max_order + 1

    res = (
        await supabase.table("paper_book_sections")
        .insert({
            "paper_book_id": paper_book_id,
            "name": payload.name,
            "order_index": order_index,
            "page_number_column": payload.page_number_column.value,
            "is_default": False,
        })
        .execute()
    )
    response = {"section": res.data}
    return Success(data=response, message="Section created successfully")


@sectionsListRouter.patch("/reorder/", dependencies=[Depends(AuthenticationRequired)])
async def reorder_sections(
    request: Request,
    paper_book_id: str,
    payload: SectionReorder,
):
    supabase = await get_supabase_client(request.state.token)
    paper_books = (
        await supabase.table("paper_books")
        .select("id")
        .eq("id", paper_book_id)
        .eq("user_id", request.state.sub)
        .single()
        .execute()
    )
    if not paper_books.data:
        raise NotFound(message="Paper book not found")

    updated = []
    for idx, section_id in enumerate(payload.ordered_ids):
        res = (
            await supabase.table("paper_book_sections")
            .update({"order_index": idx + 1})
            .eq("id", section_id)
            .eq("paper_book_id", paper_book_id)
            .execute()
        )
        if res.data:
            updated.append(res.data[0])

    response = {"updated_sections": updated}
    return Success(data=response, message="Sections reordered successfully")
