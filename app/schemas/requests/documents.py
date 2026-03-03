from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class CommitDocumentUpload(BaseModel):
    file_path: str
    file_name: str


class DocumentCreate(BaseModel):
    doc_id: str = Field(..., description="Unique identifier for the document")
    section_id: Optional[str] = None
    order_index: Optional[int] = None


class DocumentUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=500)


class DocumentAssignSection(BaseModel):
    section_id: str
    order_index: Optional[int] = None


class DocumentReorderItem(BaseModel):
    id: str
    section_id: Optional[str] = None
    order_index: int


class DocumentReorder(BaseModel):
    """List of {id, section_id, order_index} to batch update positions"""
    items: List[DocumentReorderItem]


class SplitRange(BaseModel):
    start: int = Field(..., ge=1, description="1-based start page")
    end: int = Field(..., ge=1, description="1-based end page (inclusive)")
    filename: Optional[str] = None


class DocumentSplitRequest(BaseModel):
    ranges: List[SplitRange] = Field(..., min_length=1)


class DocumentResponse(BaseModel):
    id: str
    paper_book_id: str
    section_id: Optional[str]
    original_filename: str
    storage_path: str
    file_size: Optional[int]
    order_index: int
    is_split_child: bool
    parent_document_id: Optional[str]
    split_page_start: Optional[int]
    split_page_end: Optional[int]
    uploaded_at: datetime
    updated_at: datetime


class DeletePagesRequest(BaseModel):
    page_indices: List[int] = Field(
        ...,
        min_length=1,
        description="1-based page numbers to delete",
    )
