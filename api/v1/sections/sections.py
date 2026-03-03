from fastapi import APIRouter, Depends, Request
from typing import List
from app.schemas.requests import SectionUpdate
from core.dependencies import AuthenticationRequired
from core.supabase.client import get_supabase_client
from core.responseTypes import Success, NotFound

sectionsRouter = APIRouter()


@sectionsRouter.get("/documents", dependencies=[Depends(AuthenticationRequired)])
async def get_section_documents(
    request: Request,
    paper_book_id: str,
    section_id: str,
):
    supabase = await get_supabase_client(request.state.token)
    section_docs = (
        await supabase.table("paper_book_documents")
        .select("id")
        .eq("paper_book_id", paper_book_id)
        .eq("section_id", section_id)
        .execute()
    )

    response = {"section": section_docs.data}
    return Success(data=response, message="Section documents retrieved successfully")


@sectionsRouter.patch("/", dependencies=[Depends(AuthenticationRequired)])
async def update_section(
    request: Request,
    paper_book_id: str,
    section_id: str,
    payload: SectionUpdate,
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
        .eq("id", section_id)
        .eq("paper_book_id", paper_book_id)
        .single()
        .execute()
    )
    if not res.data:
        raise NotFound(message="Section not found")

    update_data = payload.model_dump(exclude_none=True)
    if "page_number_column" in update_data:
        update_data["page_number_column"] = update_data["page_number_column"].value

    res = (
        await supabase.table("paper_book_sections")
        .update(update_data)
        .eq("id", section_id)
        .eq("paper_book_id", paper_book_id)
        .execute()
    )
    response = {"section": res.data}
    return Success(data=response, message="Section updated successfully")


@sectionsRouter.delete("/", dependencies=[Depends(AuthenticationRequired)])
async def delete_section(
    request: Request,
    paper_book_id: str,
    section_id: str,
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
        .eq("id", section_id)
        .eq("paper_book_id", paper_book_id)
        .single()
        .execute()
    )
    if not res.data:
        raise NotFound(message="Section not found")

    res = (
        await supabase.table("paper_book_sections")
        .delete()
        .eq("id", section_id)
        .execute()
    )
    return Success(data={}, message="Section deleted successfully")
