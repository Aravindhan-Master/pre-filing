from fastapi import APIRouter, Depends, Request
from app.schemas.requests import PaperBookCreate
from core.dependencies import AuthenticationRequired
from core.responseTypes import Success
from core.supabase.client import get_supabase_client

paperBooksListRouter = APIRouter()


@paperBooksListRouter.post("/", dependencies=[Depends(AuthenticationRequired)])
async def create_paper_book(
    request: Request,
    payload: PaperBookCreate,
):
    supabase = await get_supabase_client(request.state.token)

    # Create the paper book
    pb_res = (
        await supabase.from_("paper_books")
        .insert({
            "title": payload.title,
            "forum": payload.forum,
            "application_type": payload.application_type,
            "client_name": payload.client_name,
            "user_id": request.state.sub,
            "status": "draft",
        })
        .execute()
    )
    paper_book = pb_res.data[0]
    paper_book_id = paper_book["id"]

    # Auto-create default sections
    defaults_res = (
        await supabase.from_("paper_book_default_sections")
        .select("*")
        .order("order_index")
        .execute()
    )
    default_sections = defaults_res.data or []

    if default_sections:
        sections_to_insert = [
            {
                "paper_book_id": paper_book_id,
                "name": s["name"],
                "order_index": s["order_index"],
                "page_number_column": s["page_number_column"],
                "is_default": True,
            }
            for s in default_sections
        ]
        await supabase.from_("paper_book_sections").insert(sections_to_insert).execute()

    response = {"paper_book": paper_book}
    return Success(data=response, message="Paper book created successfully")


@paperBooksListRouter.get("/", dependencies=[Depends(AuthenticationRequired)])
async def list_paper_books(
    request: Request,
):
    supabase = await get_supabase_client(request.state.token)
    res = (
        await supabase.from_("paper_books")
        .select("*")
        .eq("user_id", request.state.sub)
        .order("created_at", desc=True)
        .execute()
    )
    response = {"paper_books": res.data}
    return Success(data=response, message="Paper books retrieved successfully")
