from __future__ import annotations

import json
from io import BytesIO
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from models import DownloadEvent, LabEvent, User
from schemas import LabEventOut


router = APIRouter(dependencies=[Depends(get_current_user)])


SPLIT_GRID_FILES = [
    {"file_id": "FILE-2026-001", "file_name": "split-grid-first-row.csv", "owner": "Regression Lab", "status": "ready"},
    {"file_id": "FILE-2026-002", "file_name": "split-grid-second-row.csv", "owner": "Regression Lab", "status": "ready"},
]

EMPTY_AUDIT_RECORDS = [
    {"record_id": "AUD-2026-001", "status": "passed", "summary": "Daily supplier sync"},
    {"record_id": "AUD-2026-002", "status": "passed", "summary": "Contract snapshot check"},
]


def event_to_out(event: LabEvent) -> LabEventOut:
    try:
        payload = json.loads(event.payload or "{}")
    except json.JSONDecodeError:
        payload = {"raw": event.payload}
    return LabEventOut(
        event_key=event.event_key,
        entity_id=event.entity_id,
        status=event.status,
        payload=payload,
        created_at=event.created_at,
    )


def record_event(
    db: Session,
    *,
    event_key: str,
    entity_id: str,
    status: str = "completed",
    payload: dict[str, Any] | None = None,
) -> LabEvent:
    event = LabEvent(
        event_key=event_key,
        entity_id=entity_id,
        status=status,
        payload=json.dumps(payload or {}, ensure_ascii=False),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@router.get("/events", response_model=list[LabEventOut])
def list_events(
    event_key: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[LabEventOut]:
    query = db.query(LabEvent)
    if event_key:
        query = query.filter(LabEvent.event_key == event_key)
    return [event_to_out(event) for event in query.order_by(LabEvent.created_at).all()]


@router.post("/events/{event_key}", response_model=LabEventOut)
def create_event(
    event_key: str,
    payload: dict[str, Any] | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> LabEventOut:
    body = payload or {}
    event = record_event(
        db,
        event_key=event_key,
        entity_id=str(body.get("entity_id") or event_key),
        status=str(body.get("status") or "completed"),
        payload=body,
    )
    return event_to_out(event)


@router.get("/split-grid/files")
def list_split_grid_files(_: User = Depends(get_current_user)) -> list[dict[str, str]]:
    return SPLIT_GRID_FILES


@router.post("/split-grid/open/{file_id}", response_model=LabEventOut)
def open_split_grid_file(
    file_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> LabEventOut:
    file_item = next((item for item in SPLIT_GRID_FILES if item["file_id"] == file_id), None)
    if file_item is None:
        raise HTTPException(status_code=404, detail="File not found")
    event = record_event(
        db,
        event_key="split_grid_file_opened",
        entity_id=file_item["file_id"],
        payload=file_item,
    )
    return event_to_out(event)


@router.get("/empty-audit-records")
def list_empty_audit_records(
    status: str | None = None,
    _: User = Depends(get_current_user),
) -> list[dict[str, str]]:
    if status == "failed":
        return []
    if status:
        return [item for item in EMPTY_AUDIT_RECORDS if item["status"] == status]
    return EMPTY_AUDIT_RECORDS


@router.post("/dataflow-submit", response_model=LabEventOut)
def submit_dataflow_form(
    payload: dict[str, Any],
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> LabEventOut:
    supplier_number = str(payload.get("supplier_number") or "")
    cost_center = str(payload.get("cost_center") or "")
    status = "completed" if supplier_number and cost_center == f"{supplier_number}-RPA-2026" else "invalid"
    event = record_event(
        db,
        event_key="dataflow_form_submitted",
        entity_id=str(payload.get("request_number") or "PR-2026-RPA-DATAFLOW"),
        status=status,
        payload=payload,
    )
    return event_to_out(event)


@router.post("/parameterized-contract/open", response_model=LabEventOut)
def open_parameterized_contract(
    payload: dict[str, Any],
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> LabEventOut:
    contract_number = str(payload.get("contract_number") or "")
    event = record_event(
        db,
        event_key="parameterized_contract_opened",
        entity_id=contract_number,
        status="completed" if contract_number == "CT-2026-RPA-ALT-001" else "wrong_target",
        payload=payload,
    )
    return event_to_out(event)


@router.post("/modal-supplier/save", response_model=LabEventOut)
def save_modal_supplier(
    payload: dict[str, Any],
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> LabEventOut:
    event = record_event(
        db,
        event_key="modal_supplier_saved",
        entity_id=str(payload.get("supplier_number") or "SUP-2026-MODAL"),
        status="completed",
        payload=payload,
    )
    return event_to_out(event)


@router.get("/popup-report/download")
def download_popup_report(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> StreamingResponse:
    filename = "popup_report_2026.csv"
    content = "report_id,status\nPOPUP-2026-RPA-001,downloaded\n"
    db.add(DownloadEvent(filename=filename, source="popup_report_download"))
    record_event(
        db,
        event_key="popup_report_downloaded",
        entity_id="POPUP-2026-RPA-001",
        payload={"filename": filename},
    )
    stream = BytesIO(content.encode("utf-8"))
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(stream, media_type="text/csv", headers=headers)
