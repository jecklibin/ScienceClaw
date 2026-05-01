from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Depends
from sqlalchemy.orm import Session

from auth import verify_reset_token
from database import SessionLocal
from database import ensure_app_dirs, recreate_database
from fixtures import load_fixtures, reset_downloads_dir
from routes import approvals, auth, contracts, lab, purchase_orders, purchase_requests, reports, suppliers


app = FastAPI(title="RPA Golden Evaluation Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    ensure_app_dirs()
    recreate_database()
    db: Session = SessionLocal()
    try:
        load_fixtures(db)
    finally:
        db.close()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "rpa-eval-backend"}


@app.post("/api/eval/reset", dependencies=[Depends(verify_reset_token)])
def reset_eval() -> dict[str, str]:
    recreate_database()
    reset_downloads_dir()
    db = SessionLocal()
    try:
        load_fixtures(db)
    finally:
        db.close()
    return {"status": "reset", "database": "reloaded", "downloads": "cleared"}


app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(suppliers.router, prefix="/api/suppliers", tags=["suppliers"])
app.include_router(contracts.router, prefix="/api/contracts", tags=["contracts"])
app.include_router(
    purchase_requests.router,
    prefix="/api/purchase-requests",
    tags=["purchase requests"],
)
app.include_router(
    purchase_orders.router,
    prefix="/api/purchase-orders",
    tags=["purchase orders"],
)
app.include_router(approvals.router, prefix="/api/approvals", tags=["approvals"])
app.include_router(reports.router, prefix="/api/reports", tags=["reports"])
app.include_router(lab.router, prefix="/api/lab", tags=["regression lab"])
