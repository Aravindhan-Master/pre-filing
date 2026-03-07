from fastapi import APIRouter
from .bookmarks.bookmarks import bookmarksRouter
from .paperBooks.paperBook import paperBooksRouter
from .paperBooks.paperBooksList import paperBooksListRouter
from .sections.sections import sectionsRouter
from .sections.sectionsList import sectionsListRouter
from .documents.documentsList import router as documentsListRouter
from .indexRows.indexRows import indexRowsRouter


v1_router = APIRouter()
v1_router.include_router(paperBooksListRouter, tags=["Paper Book"])
v1_router.include_router(paperBooksRouter, prefix="/{paper_book_id}", tags=["Paper Book"])
v1_router.include_router(sectionsListRouter, prefix="/{paper_book_id}/sections", tags=["Sections"])
v1_router.include_router(sectionsRouter, prefix="/{paper_book_id}/sections/{section_id}", tags=["Sections"])
v1_router.include_router(documentsListRouter, prefix="/{paper_book_id}/documents", tags=["Documents"])
v1_router.include_router(indexRowsRouter, prefix="/{paper_book_id}/index-rows", tags=["Index Rows"])
v1_router.include_router(bookmarksRouter, prefix="/{paper_book_id}/bookmarks", tags=["Bookmarks"])
