"""Deterministic Ledger Engine: credits, payments, corrections, disambiguation & queries."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from app.db.crypto import FieldCipher
from app.db.store import KhataStore, group_transactions_by_customer
from app.schemas.models import (
    CustomerBreakdownItem,
    ExtractedQuery,
    LanguageCode,
    LatencyBreakdown,
    TransactionOut,
    VoiceTurnPlan,
    VoiceTurnResponse,
)
from app.services.customer_matcher import resolve_customer
from app.services.date_resolver import resolve_date_phrase
from app.services.responsible_ai import get_ai_disclosure


def _fmt_inr(amount: float) -> str:
    if abs(amount - round(amount)) < 1e-6:
        return f"{int(round(amount)):,}"
    return f"{amount:,.2f}"


def _pick_lang(language: str, *, en: str, kn: str, hi: str) -> str:
    """Select localized response text cleanly across English, Kannada Unicode, and Hindi/default."""
    if language == "en-IN":
        return en
    if language == "kn-IN":
        return kn
    return hi


class LedgerEngine:
    """Executes structured VoiceTurnPlans deterministically across SQLite or MongoDB stores."""

    def __init__(self, db: KhataStore, cipher: FieldCipher) -> None:
        self.db = db
        self.cipher = cipher

    def get_pending_action(self, session_id: str) -> dict | None:
        return self.db.get_pending_action(session_id)

    def list_customer_names(self) -> list[str]:
        return self.db.list_customer_names()

    def _compute_customer_balance(self, conn_or_customer_id: object, customer_id: int | None = None) -> float:
        cid = int(conn_or_customer_id if customer_id is None else customer_id)
        return self.db.get_customer_balance(cid)

    def _create_customer(
        self,
        name: str,
        language: str,
        phone: str | None = None,
        reminder_opt_in: bool = False,
    ) -> dict:
        clean_name = " ".join(part.capitalize() if part.isascii() else part for part in name.strip().split())
        phone_enc = self.cipher.encrypt(phone.strip()) if phone and phone.strip() else None
        return self.db.create_customer(
            name=clean_name,
            phone_enc=phone_enc,
            reminder_opt_in=reminder_opt_in,
            preferred_language=language,
            is_synthetic=False,
        )

    def _create_or_get_customer(
        self,
        name: str,
        language: str,
        phone: str | None = None,
        reminder_opt_in: bool = False,
    ) -> tuple[dict, bool]:
        clean_name = " ".join(part.capitalize() if part.isascii() else part for part in name.strip().split())
        for existing in self.db.list_customers_with_totals():
            if existing["name"].strip().lower() == clean_name.lower():
                return existing, False
        created = self._create_customer(
            clean_name,
            language,
            phone=phone,
            reminder_opt_in=reminder_opt_in,
        )
        return created, True

    def _insert_tx(
        self,
        customer: dict,
        amount: float,
        tx_type: str,
        tx_date: str,
        session_id: str,
        raw_utterance: str,
        confidence: float,
        note: str | None = None,
    ) -> TransactionOut:
        tx_dict = self.db.create_transaction(
            customer_id=int(customer["id"]),
            customer_name=customer["name"],
            amount=float(amount),
            tx_type=tx_type,
            transaction_date=tx_date,
            status="confirmed",
            source_session_id=session_id,
            raw_utterance_enc=self.cipher.encrypt(raw_utterance),
            note=note,
            confidence=confidence,
            audit_action="create",
            audit_reason="voice_commit",
            audit_new_value={
                "customer_id": int(customer["id"]),
                "amount": round(float(amount), 2),
                "tx_type": tx_type,
                "date": tx_date,
            },
        )
        return TransactionOut(**tx_dict)

    def _commit_operations(
        self,
        ops: list[dict],
        session_id: str,
        raw_utterance: str,
        language: str,
        confidence: float,
    ) -> tuple[list[TransactionOut], list[str]]:
        committed: list[TransactionOut] = []
        created_customers: list[str] = []
        for op in ops:
            cid = op.get("resolved_customer_id")
            if cid is not None:
                customer = self.db.get_customer_by_id(int(cid))
                if customer is None:
                    customer = self._create_customer(op["customer_name"], language)
                    created_customers.append(customer["name"])
            else:
                match = resolve_customer(self.db, op["customer_name"])
                if match.matched:
                    customer = match.matched
                else:
                    customer = self._create_customer(op["customer_name"], language)
                    created_customers.append(customer["name"])

            tx = self._insert_tx(
                customer=customer,
                amount=float(op["amount"]),
                tx_type=op["tx_type"],
                tx_date=op["resolved_date"],
                session_id=session_id,
                raw_utterance=raw_utterance,
                confidence=confidence,
                note=op.get("note"),
            )
            committed.append(tx)
        return committed, created_customers

    def _format_commit_reply(
        self,
        txs: list[TransactionOut],
        created_customers: list[str],
        language: str,
    ) -> str:
        primary_cust_id = txs[0].customer_id
        primary_name = txs[0].customer_name
        new_balance = self._compute_customer_balance(primary_cust_id)
        bal_str = _fmt_inr(new_balance)

        if len(txs) == 1:
            t = txs[0]
            amt_str = _fmt_inr(t.amount)
            prefix = (
                f"ಹೊಸ ಗ್ರಾಹಕ {primary_name} ಸೇರಿಸಲಾಗಿದೆ. "
                if language == "kn-IN"
                else (f"Added new customer {primary_name}. " if language == "en-IN" else f"Naya customer {primary_name} add kiya. ")
            ) if primary_name in created_customers else ""
            if language == "kn-IN":
                action = "ಉದಾರ್" if t.tx_type == "credit" else "ಪೇಮೆಂಟ್"
                return f"{prefix}{primary_name} ಅವರ ಖಾತೆಗೆ ₹{amt_str} {action} ದಾಖಲಿಸಲಾಗಿದೆ. ಒಟ್ಟು ಬಾಕಿ ₹{bal_str} ಇದೆ."
            if language == "en-IN":
                action = "credit" if t.tx_type == "credit" else "payment"
                return f"{prefix}Recorded ₹{amt_str} {action} for {primary_name}. Updated balance is ₹{bal_str}."
            if t.tx_type == "credit":
                return f"{prefix}{primary_name} ke khate mein ₹{amt_str} udhaar jod diya. Abhi kul baaki ₹{bal_str} hai."
            return f"{prefix}{primary_name} ka ₹{amt_str} payment record kar diya. Abhi kul baaki ₹{bal_str} hai."

        # Multi-transaction summary (e.g. "Ramesh ka 340 udhaar, kal 200 diya tha")
        if language == "kn-IN":
            kn_parts = [
                f"{'ಇಂದಿನ' if t.transaction_date == date.today().isoformat() else t.transaction_date} ₹{_fmt_inr(t.amount)} {'ಉದಾರ್' if t.tx_type == 'credit' else 'ಪೇಮೆಂಟ್'}"
                for t in txs
            ]
            return f"{primary_name} ಅವರ ಖಾತೆಗೆ {' ಮತ್ತು '.join(kn_parts)} ದಾಖಲಿಸಲಾಗಿದೆ. ಒಟ್ಟು ಬಾಕಿ ₹{bal_str} ಇದೆ."
        parts = []
        for t in txs:
            label = "udhaar" if t.tx_type == "credit" else "payment"
            date_tag = "aaj" if t.transaction_date == date.today().isoformat() else t.transaction_date
            parts.append(f"{date_tag} ka ₹{_fmt_inr(t.amount)} {label}")
        joined = " aur ".join(parts)
        if language == "en-IN":
            return f"Recorded for {primary_name}: {', '.join(parts)}. Current balance is ₹{bal_str}."
        return f"{primary_name} ka {joined} record kar diya. Abhi ₹{bal_str} baaki hain."

    def _format_confirmation_prompt(
        self,
        ops: list[dict],
        language: str,
    ) -> str:
        """Build a polite assistant-style confirmation question before committing khata entries."""
        first = ops[0]
        cid = first.get("resolved_customer_id")
        if cid is not None:
            row = self.db.get_customer_by_id(int(cid))
            cust_name = row["name"] if row else first["customer_name"]
        else:
            cust_name = first["customer_name"]

        if len(ops) == 1:
            amt_str = _fmt_inr(float(first["amount"]))
            is_credit = first["tx_type"] == "credit"
            if language == "en-IN":
                action = "credit (udhaar)" if is_credit else "payment (debit)"
                return f"Sure, sir. Shall I add ₹{amt_str} {action} for {cust_name}? Say yes to confirm or no to cancel."
            if language == "kn-IN":
                action = "ಉದಾರ್" if is_credit else "ಪೇಮೆಂಟ್"
                return f"ಸರ್, {cust_name} ಅವರ ಖಾತೆಗೆ ₹{amt_str} {action} ಸೇರಿಸಲೇ? ದೃಢೀಕರಿಸಲು ಹೌದು ಅಥವಾ yes ಹೇಳಿ."
            if language == "ta-IN":
                action = "kadan" if is_credit else "varavu"
                return f"Sure sir, {cust_name} kanakkil ₹{amt_str} {action} serkkalamma? Confirm seyya yes alladhu aam sollungal."
            if language == "te-IN":
                action = "udhaar credit" if is_credit else "payment"
                return f"Sure sir, {cust_name} khatalo ₹{amt_str} {action} add cheyyana? Confirm cheyadaniki yes leda avunu cheppandi."
            action = "udhaar" if is_credit else "payment"
            return f"Sure sir, {cust_name} ke khate mein ₹{amt_str} {action} add kar doon? Confirm karne ke liye haan ya yes boliye."

        summary = ", ".join(
            f"₹{_fmt_inr(float(o['amount']))} {'credit' if o['tx_type'] == 'credit' else 'payment'}"
            for o in ops
        )
        if language == "en-IN":
            return f"Sure, sir. Shall I record {summary} for {cust_name}? Say yes to confirm or no to cancel."
        if language == "kn-IN":
            kn_summary = ", ".join(
                f"₹{_fmt_inr(float(o['amount']))} {'ಉದಾರ್' if o['tx_type'] == 'credit' else 'ಪೇಮೆಂಟ್'}"
                for o in ops
            )
            return f"ಸರ್, {cust_name} ಅವರಿಗಾಗಿ {kn_summary} ದಾಖಲಿಸಲೇ? ದೃಢೀಕರಿಸಲು ಹೌದು ಅಥವಾ yes ಹೇಳಿ."
        return f"Sure sir, {cust_name} ke liye {summary} record kar doon? Confirm karne ke liye haan ya yes boliye."

    def _row_to_tx_out(self, r: dict) -> TransactionOut:
        return TransactionOut(
            id=int(r["id"]),
            customer_id=int(r["customer_id"]),
            customer_name=str(r["customer_name"]),
            amount=round(float(r["amount"]), 2),
            tx_type=r["tx_type"],
            transaction_date=r["transaction_date"],
            status=r["status"],
            note=r.get("note"),
            confidence=float(r.get("confidence", 1.0)),
            created_at=r["created_at"],
        )

    def _summarize_date_query(
        self,
        tx_type: str,
        target_date: str,
        language: str,
    ) -> tuple[str, str | None, int | None, str | None, bool, list[TransactionOut], list[CustomerBreakdownItem]]:
        tx_rows = self.db.list_date_transactions(tx_type, target_date)
        total = round(sum(float(r["amount"]) for r in tx_rows), 2)
        grouped = group_transactions_by_customer(tx_rows)
        breakdown = [CustomerBreakdownItem(**item) for item in grouped]
        txs = [self._row_to_tx_out(r) for r in tx_rows]
        names_part = ", ".join(f"{b.customer_name} ₹{_fmt_inr(b.amount)}" for b in breakdown)
        is_today = target_date == date.today().isoformat()
        tag_hi = "Aaj" if is_today else target_date
        tag_kn = "ಇಂದು" if is_today else target_date

        if tx_type == "credit":
            if breakdown:
                reply = _pick_lang(
                    language,
                    en=(
                        f"Total credit on {target_date} is ₹{_fmt_inr(total)} across {len(breakdown)} "
                        f"customer{'s' if len(breakdown) != 1 else ''}: {names_part}."
                    ),
                    kn=f"{tag_kn} ಒಟ್ಟು ₹{_fmt_inr(total)} ಉದಾರ್ ಆಗಿದೆ ({names_part}).",
                    hi=f"{tag_hi} total ₹{_fmt_inr(total)} ka udhaar gaya ({names_part}).",
                )
            else:
                reply = _pick_lang(
                    language,
                    en=f"No credit entries were recorded on {target_date} (total ₹0).",
                    kn=f"{tag_kn} ಯಾವುದೇ ಉದಾರ್ ದಾಖಲಾಗಿಲ್ಲ (ಒಟ್ಟು ₹0).",
                    hi=f"{tag_hi} koi udhaar entry record nahi hui (total ₹0).",
                )
        else:
            if breakdown:
                reply = _pick_lang(
                    language,
                    en=(
                        f"Total payments received on {target_date} is ₹{_fmt_inr(total)} from {len(breakdown)} "
                        f"customer{'s' if len(breakdown) != 1 else ''}: {names_part}."
                    ),
                    kn=f"{tag_kn} ಒಟ್ಟು ₹{_fmt_inr(total)} ಪೇಮೆಂಟ್ ಬಂದಿದೆ ({names_part}).",
                    hi=f"{tag_hi} total ₹{_fmt_inr(total)} payment aaya ({names_part}).",
                )
            else:
                reply = _pick_lang(
                    language,
                    en=f"No payments were recorded on {target_date} (total ₹0).",
                    kn=f"{tag_kn} ಯಾವುದೇ ಪೇಮೆಂಟ್ ದಾಖಲಾಗಿಲ್ಲ (ಒಟ್ಟು ₹0).",
                    hi=f"{tag_hi} koi payment record nahi hui (total ₹0).",
                )

        single_id = breakdown[0].customer_id if len(breakdown) == 1 else None
        return reply, "home", single_id, None, False, txs, breakdown

    def _answer_query(
        self,
        query: ExtractedQuery,
        language: str,
        utterance: str,
    ) -> tuple[str, str | None, int | None, str | None, bool, list[TransactionOut], list[CustomerBreakdownItem]]:
        target_date = resolve_date_phrase(query.date_phrase, utterance_context=utterance)

        if query.kind == "all_customers":
            rows = self.db.list_customers_with_totals()
            total_outstanding = sum(float(r["total_credit"]) - float(r["total_payment"]) for r in rows)
            ranked = sorted(
                (
                    (
                        int(r["id"]),
                        r["name"],
                        round(float(r["total_credit"]) - float(r["total_payment"]), 2),
                    )
                    for r in rows
                ),
                key=lambda item: (-item[2], item[1].lower()),
            )
            breakdown = [
                CustomerBreakdownItem(customer_id=cid, customer_name=name, amount=bal, tx_count=1)
                for cid, name, bal in ranked
            ]
            all_parts = ", ".join(f"{name} ₹{_fmt_inr(bal)}" for _, name, bal in ranked) or "no customers yet"
            reply = _pick_lang(
                language,
                en=(
                    f"Showing all {len(rows)} customers on screen, sir. Total outstanding is ₹{_fmt_inr(total_outstanding)}. "
                    f"Customers: {all_parts}."
                ),
                kn=(
                    f"ಒಟ್ಟು {len(rows)} ಗ್ರಾಹಕರ ವಿವರಗಳನ್ನು ತೆರೆಯಲ್ಲಿ ತೋರಿಸಲಾಗುತ್ತಿದೆ, ಸರ್. "
                    f"ಒಟ್ಟು ಬಾಕಿ ₹{_fmt_inr(total_outstanding)} ಇದೆ. ಗ್ರಾಹಕರು: {all_parts}."
                ),
                hi=(
                    f"Kul {len(rows)} customers ki details screen par khol raha hoon. Total baaki ₹{_fmt_inr(total_outstanding)} hai. "
                    f"Customers: {all_parts}."
                ),
            )
            return reply, "customers", None, None, False, [], breakdown

        if query.kind == "credit_sum_for_date":
            return self._summarize_date_query("credit", target_date, language)

        if query.kind == "payment_sum_for_date":
            return self._summarize_date_query("payment", target_date, language)

        if query.kind == "total_outstanding":
            rows = self.db.list_customers_with_totals()
            debtors = sorted(
                (
                    (
                        int(r["id"]),
                        r["name"],
                        round(float(r["total_credit"]) - float(r["total_payment"]), 2),
                    )
                    for r in rows
                    if round(float(r["total_credit"]) - float(r["total_payment"]), 2) > 0
                ),
                key=lambda item: (-item[2], item[1].lower()),
            )
            total = round(sum(float(r["total_credit"]) - float(r["total_payment"]) for r in rows), 2)
            breakdown = [
                CustomerBreakdownItem(customer_id=cid, customer_name=name, amount=bal, tx_count=1)
                for cid, name, bal in debtors
            ]
            names_part = ", ".join(f"{name} ₹{_fmt_inr(bal)}" for _, name, bal in debtors)
            if breakdown:
                reply = _pick_lang(
                    language,
                    en=f"Total store outstanding balance is ₹{_fmt_inr(total)} across {len(breakdown)} customers: {names_part}.",
                    kn=f"ಅಂಗಡಿಯ ಒಟ್ಟು ಬಾಕಿ ಉದಾರ್ ₹{_fmt_inr(total)} ಇದೆ ({names_part}).",
                    hi=f"Dukaan ka kul baaki udhaar ₹{_fmt_inr(total)} hai ({names_part}).",
                )
            else:
                reply = _pick_lang(
                    language,
                    en=f"Total store outstanding balance is ₹{_fmt_inr(total)}. All customer balances are settled.",
                    kn=f"ಅಂಗಡಿಯ ಒಟ್ಟು ಬಾಕಿ ಉದಾರ್ ₹{_fmt_inr(total)} ಇದೆ. ಎಲ್ಲಾ ಗ್ರಾಹಕರ ಲೆಕ್ಕ ಪೂರ್ಣವಾಗಿದೆ.",
                    hi=f"Dukaan ka kul baaki udhaar ₹{_fmt_inr(total)} hai. Sabhi customers ka hisaab clear hai.",
                )
            return reply, "home", None, None, False, [], breakdown

        if query.kind == "top_debtor":
            top = self.db.get_top_debtor()
            if not top or float(top[1]) <= 0:
                reply = _pick_lang(
                    language,
                    en="No customer currently owes an outstanding balance.",
                    kn="ಸದ್ಯಕ್ಕೆ ಯಾವುದೇ ಗ್ರಾಹಕರಲ್ಲಿ ಉದಾರ್ ಬಾಕಿ ಇಲ್ಲ.",
                    hi="Abhi kisi customer par udhaar baaki nahi hai.",
                )
                return reply, "customers", None, None, False, [], []
            amt = _fmt_inr(float(top[1]))
            reply = _pick_lang(
                language,
                en=f"The highest outstanding balance is with {top[0]} at ₹{amt}.",
                kn=f"ಅತಿ ಹೆಚ್ಚು ಉದಾರ್ {top[0]} ಅವರ ಬಳಿ ₹{amt} ಇದೆ.",
                hi=f"Sabse zyada udhaar {top[0]} ke paas ₹{amt} hai.",
            )
            return reply, "customers", None, top[0], False, [], []

        # Customer balance or history
        if not query.customer_name:
            reply = _pick_lang(
                language,
                en="Which customer's account would you like to check?",
                kn="ಯಾವ ಗ್ರಾಹಕರ ಲೆಕ್ಕವನ್ನು ಪರಿಶೀಲಿಸಬೇಕು, ಸರ್?",
                hi="Kis customer ka hisaab check karna hai?",
            )
            return reply, "customers", None, None, False, [], []
        if "," in query.customer_name:
            requested_names = [part.strip() for part in query.customer_name.split(",") if part.strip()]
            parts_reply: list[str] = []
            combined_txs: list[TransactionOut] = []
            combined_breakdown: list[CustomerBreakdownItem] = []
            for req_name in requested_names:
                m = resolve_customer(self.db, req_name)
                if not m.matched:
                    continue
                cust_i = m.matched
                cid_i = int(cust_i["id"])
                bal_i = self._compute_customer_balance(cid_i)
                rows_i = [self._row_to_tx_out(r) for r in self.db.list_customer_transactions(cid_i)[:6]]
                combined_txs.extend(rows_i[:3])
                combined_breakdown.append(
                    CustomerBreakdownItem(customer_id=cid_i, customer_name=cust_i["name"], amount=bal_i, tx_count=len(rows_i))
                )
                if query.kind == "customer_history" and rows_i:
                    latest = rows_i[0]
                    parts_reply.append(f"{cust_i['name']} (balance ₹{_fmt_inr(bal_i)}; latest ₹{_fmt_inr(latest.amount)} {latest.tx_type} on {latest.transaction_date})")
                else:
                    parts_reply.append(f"{cust_i['name']} ₹{_fmt_inr(bal_i)}")
            if parts_reply:
                joined_parts = "; ".join(parts_reply)
                reply = _pick_lang(
                    language,
                    en=f"Sir, here are the details: {joined_parts}.",
                    kn=f"ಸರ್, ವಿವರಗಳು ಹೀಗಿವೆ: {joined_parts}.",
                    hi=f"Sir, details yeh hain: {joined_parts}.",
                )
                return reply, "customers", None, None, False, combined_txs, combined_breakdown

        match = resolve_customer(self.db, query.customer_name)
        if match.status == "none":
            reply = _pick_lang(
                language,
                en=f"No customer named {query.customer_name} was found in your ledger.",
                kn=f"{query.customer_name} ಹೆಸರಿನ ಗ್ರಾಹಕರು ಖಾತೆಯಲ್ಲಿ ಸಿಗಲಿಲ್ಲ.",
                hi=f"{query.customer_name} naam ka koi customer khate mein nahi mila.",
            )
            return reply, "customers", None, None, False, [], []
        if match.status == "ambiguous":
            names = [c["name"] for c in match.candidates]
            joined = f"{names[0]} {'or' if language == 'en-IN' else ('ಅಥವಾ' if language == 'kn-IN' else 'ya')} {names[1]}" if len(names) == 2 else ", ".join(names)
            reply = _pick_lang(
                language,
                en=f"Which {query.customer_name}? {joined}?",
                kn=f"ಯಾವ {query.customer_name}? {joined}?",
                hi=f"Kaunse {query.customer_name}? {joined}?",
            )
            return reply, "customers", None, None, False, [], []

        cust = match.matched
        cid = int(cust["id"])
        bal = self._compute_customer_balance(cid)
        all_cust_txs = [self._row_to_tx_out(r) for r in self.db.list_customer_transactions(cid)[:12]]
        breakdown = [CustomerBreakdownItem(customer_id=cid, customer_name=cust["name"], amount=bal, tx_count=len(all_cust_txs))]
        if query.kind == "customer_history":
            rows = self.db.get_customer_recent_confirmed_transactions(cid, limit=3)
            if not rows:
                reply = _pick_lang(
                    language,
                    en=f"{cust['name']} has no recorded transactions yet.",
                    kn=f"{cust['name']} ಅವರ ಖಾತೆಯಲ್ಲಿ ಇನ್ನೂ ಯಾವುದೇ ವ್ಯವಹಾರವಿಲ್ಲ.",
                    hi=f"{cust['name']} ke khate mein abhi koi transaction nahi hai.",
                )
            else:
                if language == "kn-IN":
                    summary = ", ".join(
                        f"{r['transaction_date']} ರಂದು ₹{_fmt_inr(float(r['amount']))} {'ಉದಾರ್' if r['tx_type']=='credit' else 'ಪೇಮೆಂಟ್'}"
                        for r in rows
                    )
                    reply = f"{cust['name']} ಅವರ ಇತ್ತೀಚಿನ ಲೆಕ್ಕ: {summary}. ಒಟ್ಟು ಬಾಕಿ ₹{_fmt_inr(bal)}."
                elif language == "en-IN":
                    summary = ", ".join(
                        f"₹{_fmt_inr(float(r['amount']))} {r['tx_type']} on {r['transaction_date']}"
                        for r in rows
                    )
                    reply = f"Recent history for {cust['name']}: {summary}. Total due: ₹{_fmt_inr(bal)}."
                else:
                    summary = ", ".join(
                        f"{r['transaction_date']} ko ₹{_fmt_inr(float(r['amount']))} {'udhaar' if r['tx_type']=='credit' else 'payment'}"
                        for r in rows
                    )
                    reply = f"{cust['name']} ka recent hisaab: {summary}. Kul baaki ₹{_fmt_inr(bal)}."
            return reply, "customers", cid, cust["name"], True, all_cust_txs, breakdown

        reply = _pick_lang(
            language,
            en=f"{cust['name']} owes ₹{_fmt_inr(bal)}.",
            kn=f"{cust['name']} ಅವರ ಒಟ್ಟು ಬಾಕಿ ₹{_fmt_inr(bal)} ಇದೆ.",
            hi=f"{cust['name']} ka total ₹{_fmt_inr(bal)} baaki hai.",
        )
        return reply, "customers", cid, cust["name"], True, all_cust_txs, breakdown

    def process_turn(
        self,
        session_id: str,
        plan: VoiceTurnPlan,
        language: LanguageCode,
        model_used: str,
        latency: LatencyBreakdown,
        ephemeral_transcripts: bool = False,
        channel: str = "voice",
        interactive_confirm: bool = False,
        fallback_pending_action: dict | None = None,
        fallback_last_tx_ids: list[int] | None = None,
    ) -> VoiceTurnResponse:
        """Apply VoiceTurnPlan deterministically and log telemetry + audit events."""
        with self.db.unit_of_work():
            session = self.db.get_or_create_session(session_id, language)
            pending = json.loads(session["pending_action_json"]) if session["pending_action_json"] else None
            if pending is None and fallback_pending_action:
                pending = fallback_pending_action
            last_tx_ids: list[int] = (
                json.loads(session["last_committed_tx_ids_json"]) if session["last_committed_tx_ids_json"] else []
            )
            if not last_tx_ids and fallback_last_tx_ids:
                last_tx_ids = list(fallback_last_tx_ids)
            now = datetime.now(timezone.utc).isoformat()

            status = "answered"
            spoken_reply = ""
            txs_out: list[TransactionOut] = []
            customer_breakdown: list[CustomerBreakdownItem] = []
            candidate_names: list[str] = []
            new_pending: dict | None = pending
            new_last_tx_ids: list[int] = last_tx_ids
            navigate_tab: str | None = None
            highlight_customer_id: int | None = None
            highlight_customer_name: str | None = None
            open_customer_details = False

            # Case A: Query
            if plan.intent == "query_ledger" and plan.query:
                (
                    spoken_reply,
                    navigate_tab,
                    highlight_customer_id,
                    highlight_customer_name,
                    open_customer_details,
                    txs_out,
                    customer_breakdown,
                ) = self._answer_query(plan.query, language, plan.transcript)
                status = "answered"

            # Case A2: Direct Page / Customer Navigation
            elif plan.intent == "navigate_page":
                page = plan.target_page or "home"
                if page not in ("home", "customers", "ledgers", "settings"):
                    page = "home"
                navigate_tab = page
                if plan.selected_customer:
                    match = resolve_customer(self.db, plan.selected_customer)
                    if match.matched:
                        navigate_tab = "customers"
                        highlight_customer_id = int(match.matched["id"])
                        highlight_customer_name = match.matched["name"]
                        open_customer_details = True
                page_labels = {
                    "home": "Khata Dashboard",
                    "customers": "Customers Details",
                    "ledgers": "All Customer Ledgers",
                    "settings": "Settings",
                }
                if open_customer_details and highlight_customer_name:
                    spoken_reply = _pick_lang(
                        language,
                        en=f"Opening {highlight_customer_name}'s khata details, sir.",
                        kn=f"{highlight_customer_name} ಅವರ ಖಾತೆ ವಿವರಗಳನ್ನು ತೆರೆಯಲಾಗುತ್ತಿದೆ, ಸರ್.",
                        hi=f"Opening {highlight_customer_name}'s khata details, sir.",
                    )
                else:
                    label = page_labels.get(page, page)
                    spoken_reply = _pick_lang(
                        language,
                        en=f"Opening {label}, sir.",
                        kn=f"{label} ಪುಟವನ್ನು ತೆರೆಯಲಾಗುತ್ತಿದೆ, ಸರ್.",
                        hi=f"Opening {label}, sir.",
                    )
                status = "answered"

            # Case A3: Create Customer via Voice / AI
            elif plan.intent == "create_customer":
                raw_name = (plan.new_customer_name or plan.selected_customer or "").strip()
                if not raw_name:
                    new_pending = {"kind": "create_customer"}
                    spoken_reply = _pick_lang(
                        language,
                        en="Sure, sir. What is the new customer's name?",
                        kn="ಖಂಡಿತ ಸರ್, ಹೊಸ ಗ್ರಾಹಕರ ಹೆಸರು ಏನೆಂದು ಸೇರಿಸಲಿ?",
                        hi="Zaroor sir, naye customer ka naam kya likhna hai?",
                    )
                    status = "needs_confirmation"
                    navigate_tab = "customers"
                else:
                    cust, created_now = self._create_or_get_customer(
                        raw_name,
                        language,
                        phone=plan.new_customer_phone,
                        reminder_opt_in=bool(plan.new_customer_opt_in),
                    )
                    highlight_customer_id = int(cust["id"])
                    highlight_customer_name = cust["name"]
                    navigate_tab = "customers"
                    new_pending = None
                    if created_now:
                        status = "recorded"
                        phone_note = " with phone saved" if plan.new_customer_phone else ""
                        spoken_reply = _pick_lang(
                            language,
                            en=f"Added new customer {cust['name']}{phone_note}, sir. Showing Customers list now.",
                            kn=f"ಹೊಸ ಗ್ರಾಹಕ {cust['name']} ಅವರನ್ನು ಖಾತೆಗೆ ಸೇರಿಸಲಾಗಿದೆ, ಸರ್.",
                            hi=f"Naya customer {cust['name']} add kar diya hai, sir.",
                        )
                    else:
                        status = "answered"
                        spoken_reply = _pick_lang(
                            language,
                            en=f"{cust['name']} is already in your customer list, sir.",
                            kn=f"{cust['name']} ಈಗಾಗಲೇ ಗ್ರಾಹಕರ ಪಟ್ಟಿಯಲ್ಲಿದ್ದಾರೆ, ಸರ್.",
                            hi=f"{cust['name']} pehle se customer list mein hain, sir.",
                        )

            # Case A4: Edit / Update Existing Customer Details via Voice / AI
            elif plan.intent == "update_customer":
                target = (plan.edit_target_customer or plan.selected_customer or "").strip()
                if not target:
                    spoken_reply = _pick_lang(
                        language,
                        en="Which customer's details would you like to edit, sir?",
                        kn="ಯಾವ ಗ್ರಾಹಕರ ವಿವರಗಳನ್ನು ಬದಲಾಯಿಸಬೇಕು, ಸರ್?",
                        hi="Kis customer ki details update karni hai, sir?",
                    )
                    status = "answered"
                    navigate_tab = "customers"
                else:
                    match = resolve_customer(self.db, target)
                    if match.status == "none":
                        spoken_reply = _pick_lang(
                            language,
                            en=f"No customer named {target} was found in your khata, sir.",
                            kn=f"{target} ಹೆಸರಿನ ಗ್ರಾಹಕರು ಖಾತೆಯಲ್ಲಿ ಸಿಗಲಿಲ್ಲ, ಸರ್.",
                            hi=f"{target} naam ka koi customer khate mein nahi mila, sir.",
                        )
                        status = "answered"
                        navigate_tab = "customers"
                    elif match.status == "ambiguous":
                        candidate_names = [c["name"] for c in match.candidates]
                        new_pending = {
                            "kind": "disambiguation_edit_customer",
                            "base_query": target,
                            "candidates": candidate_names,
                            "new_name": plan.edit_new_name,
                            "new_phone": plan.edit_new_phone,
                            "opt_in": plan.edit_opt_in,
                            "preferred_language": plan.edit_preferred_language,
                        }
                        spoken_reply = (
                            f"ಯಾವ {target}? {' ಅಥವಾ '.join(candidate_names)}?"
                            if language == "kn-IN"
                            else f"Which {target}? {' or '.join(candidate_names)}?"
                        )
                        status = "needs_disambiguation"
                        navigate_tab = "customers"
                    else:
                        cust = match.matched
                        cid = int(cust["id"])
                        has_change = any(
                            v is not None
                            for v in (
                                plan.edit_new_name,
                                plan.edit_new_phone,
                                plan.edit_opt_in,
                                plan.edit_preferred_language,
                            )
                        )
                        if not has_change:
                            new_pending = {
                                "kind": "update_customer",
                                "customer_id": cid,
                                "customer_name": cust["name"],
                            }
                            spoken_reply = _pick_lang(
                                language,
                                en=f"Sure, sir. What should I update for {cust['name']} — name, phone number, language, or reminder opt-in?",
                                kn=f"ಖಂಡಿತ ಸರ್, {cust['name']} ಅವರ ಯಾವ ವಿವರವನ್ನು ಬದಲಾಯಿಸಲಿ — ಹೆಸರು, ಫೋನ್ ನಂಬರ್, ಭಾಷೆ ಅಥವಾ ರಿಮೈಂಡರ್?",
                                hi=f"Zaroor sir, {cust['name']} ke kaunse details badalne hain — naam, phone, language ya reminder opt-in?",
                            )
                            status = "needs_confirmation"
                            navigate_tab = "customers"
                            highlight_customer_id = cid
                            highlight_customer_name = cust["name"]
                            open_customer_details = True
                        else:
                            new_name = (
                                " ".join(
                                    p.capitalize() if p.isascii() else p
                                    for p in plan.edit_new_name.strip().split()
                                )
                                if plan.edit_new_name and plan.edit_new_name.strip()
                                else cust["name"]
                            )
                            if plan.edit_new_phone is not None:
                                clean_phone = plan.edit_new_phone.strip()
                                new_phone_enc = (
                                    None
                                    if clean_phone.lower() in ("", "none", "clear", "remove")
                                    else self.cipher.encrypt(clean_phone)
                                )
                            else:
                                new_phone_enc = cust.get("phone_enc")
                            new_opt_in = (
                                bool(plan.edit_opt_in)
                                if plan.edit_opt_in is not None
                                else bool(cust.get("reminder_opt_in"))
                            )
                            new_lang = plan.edit_preferred_language or cust.get("preferred_language") or language
                            updated = self.db.update_customer(
                                cid,
                                name=new_name,
                                phone_enc=new_phone_enc,
                                reminder_opt_in=new_opt_in,
                                preferred_language=new_lang,
                            )
                            updated_name = updated["name"] if updated else new_name
                            new_pending = None
                            status = "recorded"
                            navigate_tab = "customers"
                            highlight_customer_id = cid
                            highlight_customer_name = updated_name
                            open_customer_details = True
                            changed_bits = []
                            if plan.edit_new_name:
                                changed_bits.append(f"name to {updated_name}")
                            if plan.edit_new_phone is not None:
                                changed_bits.append(f"phone to {plan.edit_new_phone.strip()}")
                            if plan.edit_opt_in is not None:
                                changed_bits.append("reminders ON" if new_opt_in else "reminders OFF")
                            if plan.edit_preferred_language:
                                changed_bits.append(f"language to {new_lang}")
                            summary = ", ".join(changed_bits)
                            spoken_reply = _pick_lang(
                                language,
                                en=f"Updated {updated_name}'s details ({summary}), sir.",
                                kn=f"{updated_name} ಅವರ ವಿವರಗಳನ್ನು ಅಪ್‌ಡೇಟ್ ಮಾಡಲಾಗಿದೆ ({summary}), ಸರ್.",
                                hi=f"{updated_name} ki details update kar di hain ({summary}), sir.",
                            )

            # Case A5: Refuse AI Customer Delete (manual-only policy)
            elif plan.intent == "delete_customer_blocked":
                new_pending = None
                status = "answered"
                navigate_tab = "customers"
                spoken_reply = _pick_lang(
                    language,
                    en="For safety, deleting a customer can only be done manually using the Delete button on the Customers page, sir.",
                    kn="ಸುರಕ್ಷತೆಗಾಗಿ ಗ್ರಾಹಕರನ್ನು ಡಿಲೀಟ್ ಮಾಡಲು Customers ಪುಟದಲ್ಲಿರುವ Delete ಬಟನ್ ಬಳಸಿ ಕೈಯಾರೆ ಮಾತ್ರ ಮಾಡಬಹುದು, ಸರ್.",
                    hi="Safety ke liye customer delete sirf Customers screen par Delete button se manually hi kiya ja sakta hai, sir.",
                )

            # Case B: Disambiguation reply ("Ramesh Gowda")
            elif plan.intent == "disambiguation_reply" and pending and pending.get("kind") == "disambiguation":
                selected = plan.selected_customer or plan.transcript
                match = resolve_customer(self.db, selected)
                if match.matched:
                    ops = pending["operations"]
                    for op in ops:
                        if op.get("needs_disambiguation"):
                            op["resolved_customer_id"] = match.matched["id"]
                            op["customer_name"] = match.matched["name"]
                    txs_out, created = self._commit_operations(
                        ops, session_id, plan.transcript, language, plan.confidence
                    )
                    spoken_reply = self._format_commit_reply(txs_out, created, language)
                    status = "recorded"
                    new_pending = None
                    new_last_tx_ids = [t.id for t in txs_out]
                else:
                    candidate_names = pending.get("candidates", [])
                    spoken_reply = _pick_lang(
                        language,
                        en=f"Please specify clearly: {' or '.join(candidate_names)}?",
                        kn=f"ದಯವಿಟ್ಟು ಹೆಸರನ್ನು ಸ್ಪಷ್ಟವಾಗಿ ಹೇಳಿ: {' ಅಥವಾ '.join(candidate_names)}?",
                        hi=f"Kripya naam clear boliye: {' ya '.join(candidate_names)}?",
                    )
                    status = "needs_disambiguation"

            # Case B2: Friendly Greeting & Conversational Check-ins
            elif plan.intent == "greeting":
                spoken_reply = (plan.clarification_question or "").strip() or _pick_lang(
                    language,
                    en="Namaste, sir! Tell me a credit or payment entry, or ask about today's khata.",
                    kn="ನಮಸ್ಕಾರ ಸರ್! ಉದಾರ್ ಅಥವಾ ಪೇಮೆಂಟ್ ಎಂಟ್ರಿ ಹೇಳಿ, ಅಥವಾ ಇಂದಿನ ಖಾತೆ ವಿವರ ಕೇಳಿ.",
                    hi="Namaste, sir! Boliye, kya udhaar ya payment likhna hai, ya aaj ka hisaab dekhna hai?",
                )
                pending_kind = new_pending.get("kind") if isinstance(new_pending, dict) else None
                status = (
                    "needs_confirmation"
                    if pending_kind in ("confirmation", "create_customer", "update_customer")
                    else "answered"
                )

            # Case C: Confirmation reply ("haan" / "nahi" / "No, cancel" — works even if session state reset)
            elif plan.intent == "confirmation_reply":
                if pending and pending.get("kind") == "confirmation":
                    if plan.confirmed:
                        txs_out, created = self._commit_operations(
                            pending["operations"], session_id, plan.transcript, language, plan.confidence
                        )
                        spoken_reply = self._format_commit_reply(txs_out, created, language)
                        status = "recorded"
                        new_pending = None
                        new_last_tx_ids = [t.id for t in txs_out]
                    else:
                        spoken_reply = _pick_lang(
                            language,
                            en="Okay, transaction cancelled. Nothing was recorded.",
                            kn="ಸರಿ, ವ್ಯವಹಾರವನ್ನು ರದ್ದುಗೊಳಿಸಲಾಗಿದೆ. ಯಾವುದೇ ಎಂಟ್ರಿ ದಾಖಲಾಗಿಲ್ಲ.",
                            hi="Theek hai, transaction cancel kar diya. Koi entry record nahi hui.",
                        )
                        status = "cancelled"
                        new_pending = None
                elif pending and pending.get("kind") in ("create_customer", "update_customer", "disambiguation_edit_customer"):
                    spoken_reply = _pick_lang(
                        language,
                        en="Cancelled customer update, sir.",
                        kn="ಗ್ರಾಹಕರ ಅಪ್‌ಡೇಟ್ ರದ್ದುಗೊಳಿಸಲಾಗಿದೆ, ಸರ್.",
                        hi="Customer update cancel kar diya, sir.",
                    )
                    status = "cancelled"
                    new_pending = None
                else:
                    new_pending = None
                    if plan.confirmed is False:
                        spoken_reply = _pick_lang(
                            language,
                            en="Okay, cancelled, sir.",
                            kn="ಸರಿ, ರದ್ದುಗೊಳಿಸಲಾಗಿದೆ, ಸರ್.",
                            hi="Theek hai, cancel kar diya, sir.",
                        )
                        status = "cancelled"
                    else:
                        spoken_reply = _pick_lang(
                            language,
                            en="There is no pending transaction to confirm right now, sir. Please say the customer and amount.",
                            kn="ಸದ್ಯಕ್ಕೆ ದೃಢೀಕರಿಸಲು ಯಾವುದೇ ಬಾಕಿ ಎಂಟ್ರಿ ಇಲ್ಲ, ಸರ್. ದಯವಿಟ್ಟು ಗ್ರಾಹಕರ ಹೆಸರು ಮತ್ತು ಮೊತ್ತವನ್ನು ಹೇಳಿ.",
                            hi="Abhi confirm karne ke liye koi pending entry nahi hai, sir. Kripya customer aur amount dobara boliye.",
                        )
                        status = "answered"

            # Case D: Spoken correction ("nahi, 430")
            elif plan.intent == "correction":
                if pending and pending.get("operations"):
                    ops = pending["operations"]
                    if plan.corrected_amount is not None:
                        ops[-1]["amount"] = plan.corrected_amount
                    if plan.corrected_tx_type is not None:
                        ops[-1]["tx_type"] = plan.corrected_tx_type
                    if plan.corrected_customer:
                        ops[-1]["customer_name"] = plan.corrected_customer
                        ops[-1]["resolved_customer_id"] = None
                        ops[-1]["needs_disambiguation"] = False

                    if pending.get("kind") == "disambiguation" and any(o.get("needs_disambiguation") for o in ops):
                        candidate_names = pending.get("candidates", [])
                        new_pending = pending
                        amt_note = f"₹{_fmt_inr(float(ops[-1]['amount']))}"
                        base_q = pending.get("base_query", "customer")
                        spoken_reply = _pick_lang(
                            language,
                            en=f"Okay, updated amount to {amt_note}. Which {base_q}? {' or '.join(candidate_names)}?",
                            kn=f"ಸರಿ, ಮೊತ್ತವನ್ನು {amt_note} ಗೆ ಬದಲಾಯಿಸಲಾಗಿದೆ. ಯಾವ {base_q}? {' ಅಥವಾ '.join(candidate_names)}?",
                            hi=f"Theek hai, amount {amt_note} kar diya. Kaunse {base_q}? {' ya '.join(candidate_names)}?",
                        )
                        status = "needs_disambiguation"
                    else:
                        txs_out, created = self._commit_operations(
                            ops, session_id, plan.transcript, language, plan.confidence
                        )
                        prefix_corr = _pick_lang(
                            language,
                            en="Updated and recorded.",
                            kn="ಸರಿ, ಸರಿಪಡಿಸಿ ದಾಖಲಿಸಲಾಗಿದೆ.",
                            hi="Theek hai, update karke record kar diya.",
                        )
                        spoken_reply = f"{prefix_corr} {self._format_commit_reply(txs_out, created, language)}"
                        status = "corrected"
                        new_pending = None
                        new_last_tx_ids = [t.id for t in txs_out]
                elif last_tx_ids:
                    target_tx_id = last_tx_ids[-1]
                    tx_row = self.db.get_transaction_with_customer(target_tx_id)
                    if tx_row:
                        old_amt = float(tx_row["amount"])
                        old_type = tx_row["tx_type"]
                        new_amt = float(plan.corrected_amount) if plan.corrected_amount is not None else old_amt
                        new_type = plan.corrected_tx_type or old_type
                        self.db.correct_transaction(
                            target_tx_id,
                            new_amount=new_amt,
                            new_tx_type=new_type,
                            old_amount=old_amt,
                            old_tx_type=old_type,
                            reason=f"spoken_correction: {plan.transcript}",
                            updated_at=now,
                        )
                        bal = self._compute_customer_balance(int(tx_row["customer_id"]))
                        txs_out = [
                            TransactionOut(
                                id=target_tx_id,
                                customer_id=int(tx_row["customer_id"]),
                                customer_name=tx_row["customer_name"],
                                amount=round(new_amt, 2),
                                tx_type=new_type,
                                transaction_date=tx_row["transaction_date"],
                                status=tx_row["status"],
                                note=tx_row["note"],
                                confidence=float(tx_row["confidence"]),
                                created_at=tx_row["created_at"],
                            )
                        ]
                        spoken_reply = _pick_lang(
                            language,
                            en=f"Updated {tx_row['customer_name']}'s amount to ₹{_fmt_inr(new_amt)}. New balance is ₹{_fmt_inr(bal)}.",
                            kn=f"ಸರಿ, {tx_row['customer_name']} ಅವರ ಮೊತ್ತವನ್ನು ₹{_fmt_inr(new_amt)} ಗೆ ಬದಲಾಯಿಸಲಾಗಿದೆ. ಹೊಸ ಬಾಕಿ ₹{_fmt_inr(bal)} ಇದೆ.",
                            hi=(
                                f"Theek hai, {tx_row['customer_name']} ka amount ₹{_fmt_inr(new_amt)} kar diya. "
                                f"Naya baaki balance ₹{_fmt_inr(bal)} hai."
                            ),
                        )
                        status = "corrected"
                    else:
                        spoken_reply = _pick_lang(
                            language,
                            en="Could not find the previous entry to correct.",
                            kn="ತಿದ್ದುಪಡಿ ಮಾಡಲು ಹಿಂದಿನ ಎಂಟ್ರಿ ಸಿಗಲಿಲ್ಲ.",
                            hi="Pichhli entry nahi mili jise sudhara ja sake.",
                        )
                        status = "error"
                else:
                    spoken_reply = _pick_lang(
                        language,
                        en="No recent transaction found to correct. Please say the customer and amount again.",
                        kn="ತಿದ್ದುಪಡಿ ಮಾಡಲು ಇತ್ತೀಚಿನ ವ್ಯವಹಾರ ಸಿಗಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಗ್ರಾಹಕರ ಹೆಸರು ಮತ್ತು ಮೊತ್ತವನ್ನು ಮತ್ತೊಮ್ಮೆ ಹೇಳಿ.",
                        hi="Sudharne ke liye koi recent transaction nahi mili. Kripya customer aur amount dobara boliye.",
                    )
                    status = "error"

            # Case E: Record one or more new transactions
            elif plan.intent == "record_transactions" and plan.operations:
                prepared_ops: list[dict] = []
                ambiguous_customer_msg: str | None = None
                ambiguous_confirm_msg: str | None = None

                for op in plan.operations:
                    resolved_date = resolve_date_phrase(op.date_phrase, utterance_context=plan.transcript)
                    match = resolve_customer(self.db, op.customer_name)
                    op_dict = {
                        "customer_name": op.customer_name,
                        "amount": float(op.amount),
                        "tx_type": op.tx_type,
                        "resolved_date": resolved_date,
                        "note": op.note,
                        "needs_disambiguation": False,
                        "resolved_customer_id": match.matched["id"] if match.matched else None,
                    }
                    if match.status == "ambiguous" and not ambiguous_customer_msg:
                        op_dict["needs_disambiguation"] = True
                        candidate_names = [c["name"] for c in match.candidates]
                        sep = " or " if language == "en-IN" else (" ಅಥವಾ " if language == "kn-IN" else " ya ")
                        c_str = candidate_names[0] + sep + candidate_names[1] if len(candidate_names) == 2 else ", ".join(candidate_names)
                        ambiguous_customer_msg = _pick_lang(
                            language,
                            en=f"Which {op.customer_name}? {c_str}?",
                            kn=f"ಯಾವ {op.customer_name}? {c_str}?",
                            hi=f"Kaunse {op.customer_name}? {c_str}?",
                        )
                    elif op.direction_ambiguous and not ambiguous_confirm_msg:
                        ambiguous_confirm_msg = _pick_lang(
                            language,
                            en=f"Should I add ₹{_fmt_inr(op.amount)} credit for {op.customer_name}? Say yes or no.",
                            kn=f"{op.customer_name} ಅವರ ಖಾತೆಗೆ ₹{_fmt_inr(op.amount)} ಉದಾರ್ ಬರೆಯಲೇ? ಹೌದು ಅಥವಾ ಇಲ್ಲ ಹೇಳಿ.",
                            hi=f"Kya {op.customer_name} ke khate mein ₹{_fmt_inr(op.amount)} udhaar likhna hai? Haan ya nahi boliye.",
                        )
                    elif op.ambiguous_amounts and len(op.ambiguous_amounts) >= 2 and plan.confidence < 0.80:
                        alts_sep = " ಅಥವಾ " if language == "kn-IN" else (" or " if language == "en-IN" else " ya ")
                        alts = alts_sep.join(f"₹{_fmt_inr(a)}" for a in op.ambiguous_amounts[:2])
                        ambiguous_confirm_msg = _pick_lang(
                            language,
                            en=f"Due to noise, please confirm: {op.customer_name} for {alts}?",
                            kn=f"ಶಬ್ದದ ಕಾರಣ ದಯವಿಟ್ಟು ದೃಢೀಕರಿಸಿ: {op.customer_name} ಅವರಿಗೆ {alts}?",
                            hi=f"Noise ki wajah se confirm kijiye: {op.customer_name} ka {alts}?",
                        )

                    prepared_ops.append(op_dict)

                if ambiguous_customer_msg:
                    new_pending = {
                        "kind": "disambiguation",
                        "base_query": plan.operations[0].customer_name,
                        "candidates": candidate_names,
                        "operations": prepared_ops,
                    }
                    spoken_reply = ambiguous_customer_msg
                    status = "needs_disambiguation"
                elif ambiguous_confirm_msg:
                    new_pending = {
                        "kind": "confirmation",
                        "operations": prepared_ops,
                    }
                    spoken_reply = ambiguous_confirm_msg
                    status = "needs_confirmation"
                elif interactive_confirm:
                    new_pending = {
                        "kind": "confirmation",
                        "operations": prepared_ops,
                    }
                    spoken_reply = self._format_confirmation_prompt(prepared_ops, language)
                    status = "needs_confirmation"
                else:
                    txs_out, created = self._commit_operations(
                        prepared_ops, session_id, plan.transcript, language, plan.confidence
                    )
                    spoken_reply = self._format_commit_reply(txs_out, created, language)
                    status = "recorded"
                    new_pending = None
                    new_last_tx_ids = [t.id for t in txs_out]

            else:
                spoken_reply = plan.clarification_question or _pick_lang(
                    language,
                    en="Sorry, I didn't catch that. Try saying: 'Add 340 credit to Ramesh' or 'Who took credit today?'",
                    kn="ಕ್ಷಮಿಸಿ, ಸ್ಪಷ್ಟವಾಗಲಿಲ್ಲ. ಹೀಗೆ ಹೇಳಿ: 'ರಮೇಶ್ ಅವರಿಗೆ 340 ಉದಾರ್' ಅಥವಾ 'ಇವತ್ತು ಎಷ್ಟು ಉದಾರ್ ಆಗಿದೆ?'",
                    hi="Samajh nahi aaya. Jaise boliye: 'Ramesh ka 340 udhaar' ya 'Aaj kitna udhaar gaya?'",
                )
                pending_kind = new_pending.get("kind") if isinstance(new_pending, dict) else None
                if pending_kind in ("confirmation", "create_customer", "update_customer"):
                    status = "needs_confirmation"
                elif pending_kind in ("disambiguation", "disambiguation_edit_customer"):
                    status = "needs_disambiguation"
                else:
                    status = "answered"

            # Enforce Responsible AI first-turn disclosure
            disclosed_now = False
            if int(session["ai_disclosed"]) == 0:
                spoken_reply = f"{get_ai_disclosure(language)} {spoken_reply}".strip()
                disclosed_now = True

            self.db.update_session(
                session_id,
                language=language,
                pending_action=new_pending,
                last_committed_tx_ids=new_last_tx_ids,
                updated_at=now,
            )

            # Log voice turn (encrypted at rest; omitted when ephemeral mode is enabled)
            voice_log_id: int | None = None
            if not ephemeral_transcripts:
                voice_log_id = self.db.insert_voice_log(
                    session_id=session_id,
                    channel=channel,
                    utterance_enc=self.cipher.encrypt(plan.transcript),
                    parsed_json=plan.model_dump_json(),
                    ai_reply_enc=self.cipher.encrypt(spoken_reply),
                    model_used=model_used,
                    latency_ms=latency.total_ms,
                    created_at=now,
                )

            if txs_out and highlight_customer_id is None and plan.intent != "query_ledger":
                highlight_customer_id = txs_out[0].customer_id
                highlight_customer_name = txs_out[0].customer_name

            return VoiceTurnResponse(
                session_id=session_id,
                transcript=plan.transcript,
                intent=plan.intent,
                status=status,
                spoken_reply=spoken_reply,
                ai_disclosure_prepended=disclosed_now,
                model_used=model_used,
                transactions=txs_out,
                customer_breakdown=customer_breakdown,
                candidate_customers=candidate_names,
                pending_action=new_pending,
                voice_log_id=voice_log_id,
                navigate_tab=navigate_tab,  # type: ignore[arg-type]
                highlight_customer_id=highlight_customer_id,
                highlight_customer_name=highlight_customer_name,
                open_customer_details=open_customer_details,
                latency=latency,
            )

    def update_voice_log_latency(self, voice_log_id: int | None, total_ms: int) -> None:
        """Persist measured end-to-end latency on the encrypted voice log entry."""
        if voice_log_id is None:
            return
        self.db.update_voice_log_latency(voice_log_id, total_ms)
