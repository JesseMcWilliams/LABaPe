"""Browser-side validation failures, reported for the admin Errors page.

The web UI checks fields before sending; when it blocks a submit, it
reports what it refused here, so problems users hit are visible even when
the server never saw the request (question 59)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ..errors import record
from ..security import Principal, current_principal

router = APIRouter(prefix="/api/errors", tags=["errors"])


class FieldProblem(BaseModel):
    field: str = Field(max_length=200)
    label: str = Field(default="", max_length=200)
    message: str = Field(max_length=1000)


class ReportIn(BaseModel):
    page: str = Field(max_length=200)
    message: str = Field(default="", max_length=2000)
    fields: list[FieldProblem] = Field(default=[], max_length=50)


@router.post("/report")
def report(body: ReportIn, request: Request, _: Principal = Depends(current_principal)):
    summary = body.message or f"{len(body.fields)} field(s) refused in the browser on {body.page}"
    request.scope["path"] = body.page[:255]  # record() logs the page the user was on
    record(request, 0, summary, [f.model_dump() for f in body.fields], origin="browser")
    return {"ok": True}
