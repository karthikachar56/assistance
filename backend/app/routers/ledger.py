"""Ledger, customers, manual transactions, polite opt-in reminders & CSV export endpoints."""

from __future__ import annotations

import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.deps import AppContainer, get_container
from app.schemas.models import (
    CustomerBreakdownItem,
    CustomerCreate,
    CustomerOut,
    CustomerUpdate,
    DashboardSummary,
    ManualTransactionCreate,
    ReminderRequest,
    ReminderResponse,
    TransactionOut,
)
from app.services.responsible_ai import build_polite_reminder


router = APIRouter(prefix="/api", tags=["ledger"])


def _row_to_customer(row, container: AppContainer) -> CustomerOut:
    credit = float(row["total_credit"] or 0)
    payment = float(row["total_payment"] or 0)
    return CustomerOut(
        id=int(row["id"]),
        name=row["name"],
        phone=container.cipher.decrypt(row["phone_enc"]),
        reminder_opt_in=bool(row["reminder_opt_in"]),
        preferred_language=row["preferred_language"],
        total_credit=round(credit, 2),
        total_payment=round(payment, 2),
        outstanding_balance=round(credit - payment, 2),
        is_synthetic=bool(row["is_synthetic"]),
        created_at=row["created_at"],
    )


def _row_to_tx(row: dict) -> TransactionOut:
    return TransactionOut(
        id=int(row["id"]),
        customer_id=int(row["customer_id"]),
        customer_name=row["customer_name"],
        amount=float(row["amount"]),
        tx_type=row["tx_type"],
        transaction_date=row["transaction_date"],
        status=row["status"],
        note=row["note"],
        confidence=float(row["confidence"]),
        created_at=row["created_at"],
    )


@router.get("/dashboard", response_model=DashboardSummary)
def get_dashboard(container: AppContainer = Depends(get_container)) -> DashboardSummary:
    summary = container.db.get_dashboard_summary(today_str=date.today().isoformat(), recent_limit=12)
    recent = [_row_to_tx(r) for r in summary["recent_transactions"]]
    return DashboardSummary(
        today_credit=summary["today_credit"],
        today_payment=summary["today_payment"],
        total_outstanding=summary["total_outstanding"],
        total_customers=summary["total_customers"],
        recent_transactions=recent,
        today_credit_customers=[CustomerBreakdownItem(**item) for item in summary.get("today_credit_customers", [])],
        today_payment_customers=[CustomerBreakdownItem(**item) for item in summary.get("today_payment_customers", [])],
        outstanding_customers=[CustomerBreakdownItem(**item) for item in summary.get("outstanding_customers", [])],
    )


@router.get("/customers", response_model=list[CustomerOut])
def list_customers(q: str = "", container: AppContainer = Depends(get_container)) -> list[CustomerOut]:
    rows = container.db.list_customers_with_totals(search_query=q)
    return [_row_to_customer(r, container) for r in rows]


@router.post("/customers", response_model=CustomerOut, status_code=201)
def create_customer(req: CustomerCreate, container: AppContainer = Depends(get_container)) -> CustomerOut:
    row = container.db.create_customer(
        name=req.name.strip(),
        phone_enc=container.cipher.encrypt(req.phone.strip()) if req.phone else None,
        reminder_opt_in=req.reminder_opt_in,
        preferred_language=req.preferred_language,
        is_synthetic=False,
    )
    return _row_to_customer(row, container)


@router.patch("/customers/{customer_id}", response_model=CustomerOut)
def update_customer(
    customer_id: int,
    req: CustomerUpdate,
    container: AppContainer = Depends(get_container),
) -> CustomerOut:
    existing = container.db.get_customer_by_id(customer_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Customer not found.")
    name = req.name.strip() if req.name else existing["name"]
    if req.phone is not None:
        cleaned_phone = req.phone.strip()
        phone_enc = container.cipher.encrypt(cleaned_phone) if cleaned_phone else None
    else:
        phone_enc = existing["phone_enc"]
    opt_in = req.reminder_opt_in if req.reminder_opt_in is not None else bool(existing["reminder_opt_in"])
    lang = req.preferred_language or existing["preferred_language"]
    row = container.db.update_customer(
        customer_id,
        name=name,
        phone_enc=phone_enc,
        reminder_opt_in=opt_in,
        preferred_language=lang,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Customer not found.")
    return _row_to_customer(row, container)


@router.delete("/customers/{customer_id}")
def delete_customer(customer_id: int, container: AppContainer = Depends(get_container)) -> dict:
    """Manual-only customer deletion endpoint (AI assistants never invoke delete)."""
    deleted = container.db.delete_customer(customer_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Customer not found.")
    return {"status": "deleted", "customer_id": customer_id}


@router.get("/customers/{customer_id}/transactions", response_model=list[TransactionOut])
def get_customer_transactions(customer_id: int, container: AppContainer = Depends(get_container)) -> list[TransactionOut]:
    rows = container.db.list_customer_transactions(customer_id)
    return [_row_to_tx(r) for r in rows]


@router.get("/transactions", response_model=list[TransactionOut])
def list_transactions(
    customer_id: int | None = None,
    container: AppContainer = Depends(get_container),
) -> list[TransactionOut]:
    rows = (
        container.db.list_customer_transactions(customer_id)
        if customer_id is not None
        else container.db.list_all_transactions()
    )
    return [_row_to_tx(r) for r in rows]


@router.post("/transactions", response_model=TransactionOut, status_code=201)
def create_manual_transaction(
    req: ManualTransactionCreate,
    container: AppContainer = Depends(get_container),
) -> TransactionOut:
    tx_date = req.transaction_date or date.today().isoformat()
    cust = container.db.get_customer_by_id(req.customer_id)
    if not cust:
        raise HTTPException(status_code=404, detail="Customer not found.")
    tx = container.db.create_transaction(
        customer_id=req.customer_id,
        customer_name=cust["name"],
        amount=round(req.amount, 2),
        tx_type=req.tx_type,
        transaction_date=tx_date,
        status="confirmed",
        note=req.note,
        confidence=1.0,
        audit_action="create",
        audit_reason="manual_entry",
        audit_new_value={"amount": req.amount, "tx_type": req.tx_type, "date": tx_date},
    )
    return TransactionOut(**tx)


@router.post("/reminders/send", response_model=ReminderResponse)
def send_polite_reminder(req: ReminderRequest, container: AppContainer = Depends(get_container)) -> ReminderResponse:
    row = container.db.get_customer_with_totals(req.customer_id)
    if not row:
        raise HTTPException(status_code=404, detail="Customer not found.")
    opted_in = bool(row["reminder_opt_in"])
    bal = round(float(row["total_credit"] or 0) - float(row["total_payment"] or 0), 2)
    if not opted_in:
        raise HTTPException(
            status_code=403,
            detail="Responsible AI Guardrail: Reminders can only be sent to customers who opted in.",
        )
    if bal <= 0:
        raise HTTPException(status_code=400, detail="Customer has no outstanding balance.")
    msg = build_polite_reminder(row["name"], bal, req.language)
    return ReminderResponse(
        customer_id=int(row["id"]),
        customer_name=row["name"],
        outstanding_balance=bal,
        message=msg,
        opted_in=True,
        sent=True,
    )


@router.get("/audit")
def list_audit_logs(limit: int = 50, container: AppContainer = Depends(get_container)) -> list[dict]:
    return container.db.list_audit_logs(limit=limit)


@router.get("/export/csv")
def export_ledger_csv(customer_id: int | None = None, container: AppContainer = Depends(get_container)):
    rows = container.db.export_transactions(customer_id=customer_id)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["ID", "Customer", "Date", "Type", "Amount (INR)", "Status", "Note", "Recorded At"])
    for r in rows:
        writer.writerow([r["id"], r["customer_name"], r["transaction_date"], r["tx_type"], r["amount"], r["status"], r["note"] or "", r["created_at"]])

    buf.seek(0)
    filename = f"voice_khata_{customer_id or 'all'}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

