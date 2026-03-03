from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class IndexRowCreate(BaseModel):
    section_id: Optional[str] = None
    sl_no: Optional[str] = Field(None, max_length=20)
    particulars: str = Field(..., min_length=1, max_length=500)
    page_start_part1: Optional[int] = None
    page_end_part1: Optional[int] = None
    page_start_part2: Optional[int] = None
    page_end_part2: Optional[int] = None
    remarks: Optional[str] = None
    order_index: Optional[int] = None


class IndexRowUpdate(BaseModel):
    sl_no: Optional[str] = Field(None, max_length=20)
    particulars: Optional[str] = Field(None, min_length=1, max_length=500)
    description: Optional[str] = Field(None, max_length=1000)
    page_start_part1: Optional[int] = None
    page_end_part1: Optional[int] = None
    page_start_part2: Optional[int] = None
    page_end_part2: Optional[int] = None
    remarks: Optional[str] = None


class IndexReorder(BaseModel):
    ordered_ids: List[str] = Field(..., description="Index row IDs in desired order")


class IndexRowResponse(BaseModel):
    id: str
    paper_book_id: str
    section_id: Optional[str]
    sl_no: Optional[str]
    particulars: str
    page_start_part1: Optional[int]
    page_end_part1: Optional[int]
    page_start_part2: Optional[int]
    page_end_part2: Optional[int]
    remarks: Optional[str]
    order_index: int
    is_custom: bool
    is_edited: bool
    created_at: datetime
    updated_at: datetime
