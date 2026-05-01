from datetime import datetime

from pydantic import BaseModel, Field


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    username: str
    display_name: str
    role: str
    department: str

    model_config = {"from_attributes": True}


class SupplierOut(BaseModel):
    number: str
    name: str
    status: str
    category: str
    region: str
    risk_level: str
    compliance_rating: str
    contact_person: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None

    model_config = {"from_attributes": True}


class SupplierUpdate(BaseModel):
    contact_person: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    status: str | None = None


class ContractOut(BaseModel):
    number: str
    title: str
    contract_type: str
    status: str
    supplier_number: str
    supplier_name: str
    amount: float
    currency: str
    owner_department: str
    start_date: str
    end_date: str
    compliance_clause: str


class PurchaseRequestItemIn(BaseModel):
    name: str
    quantity: int = Field(gt=0)
    unit_price: float = Field(gt=0)
    cost_center: str


class PurchaseRequestCreate(BaseModel):
    title: str
    contract_number: str
    department: str
    requester: str
    items: list[PurchaseRequestItemIn]


class PurchaseRequestItemOut(PurchaseRequestItemIn):
    pass


class PurchaseRequestOut(BaseModel):
    number: str
    title: str
    contract_number: str
    supplier_name: str
    department: str
    requester: str
    status: str
    total_amount: float
    created_at: datetime
    items: list[PurchaseRequestItemOut]


class PurchaseOrderOut(BaseModel):
    number: str
    request_number: str
    supplier_number: str
    supplier_name: str
    status: str
    priority: str
    total_amount: float
    created_at: datetime


class ApprovalOut(BaseModel):
    task_id: str
    purchase_order_number: str
    assignee: str
    status: str
    decision: str | None = None
    comment: str | None = None
    updated_at: datetime


class ApprovalDecision(BaseModel):
    comment: str | None = None


class ReportJobOut(BaseModel):
    job_id: str
    report_type: str
    status: str
    filename: str
    poll_count: int
    created_at: datetime
    completed_at: datetime | None = None


class DownloadEventOut(BaseModel):
    filename: str
    source: str
    created_at: datetime

    model_config = {"from_attributes": True}


class LabEventOut(BaseModel):
    event_key: str
    entity_id: str
    status: str
    payload: dict
    created_at: datetime
