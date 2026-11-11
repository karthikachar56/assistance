"""Synthetic demo data seeder for Voice Khata (DPDP-compliant fictional store)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.db.crypto import FieldCipher
from app.db.store import KhataStore, normalize_name


def seed_synthetic_data(store: KhataStore, cipher: FieldCipher) -> None:
    """Populate fictional kirana store data only when the database is empty."""
    with store.unit_of_work():
        if store.count_customers() > 0:
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        today = date.today()
        yesterday = (today - timedelta(days=1)).isoformat()
        two_days_ago = (today - timedelta(days=2)).isoformat()
        today_str = today.isoformat()

        # Intentionally includes two "Ramesh" entries so entity disambiguation can be demonstrated live.
        customers = [
            ("Ramesh Kumar", "+91-9000000001", True, "hi-IN"),
            ("Ramesh Gowda", "+91-9000000002", True, "kn-IN"),
            ("Suresh Patel", "+91-9000000003", False, "hi-IN"),
            ("Mahesh Verma", "+91-9000000004", True, "hi-IN"),
            ("Lakshmi Devi", "+91-9000000005", False, "kn-IN"),
            ("Mohan Rao", "+91-9000000006", True, "en-IN"),
        ]

        customer_ids: dict[str, int] = {}
        for name, phone, opt_in, lang in customers:
            created = store.create_customer(
                name=name,
                phone_enc=cipher.encrypt(phone),
                reminder_opt_in=opt_in,
                preferred_language=lang,
                is_synthetic=True,
                created_at=now_iso,
            )
            customer_ids[name] = int(created["id"])

        seed_txs = [
            ("Ramesh Kumar", 660.0, "credit", yesterday, "Synthetic demo seed: Rice & dal"),
            ("Ramesh Kumar", 400.0, "payment", yesterday, "Synthetic demo seed: Cash"),
            ("Ramesh Gowda", 520.0, "credit", two_days_ago, "Synthetic demo seed: Cooking oil"),
            ("Suresh Patel", 850.0, "credit", yesterday, "Synthetic demo seed: Groceries"),
            ("Suresh Patel", 200.0, "payment", today_str, "Synthetic demo seed: UPI payment"),
            ("Mahesh Verma", 150.0, "credit", today_str, "Synthetic demo seed: Milk & bread"),
            ("Lakshmi Devi", 480.0, "credit", two_days_ago, "Synthetic demo seed: Atta & sugar"),
            ("Mohan Rao", 900.0, "credit", yesterday, "Synthetic demo seed: Monthly ration"),
            ("Mohan Rao", 300.0, "payment", today_str, "Synthetic demo seed: Partial cash"),
        ]

        for cname, amount, tx_type, tx_date, note in seed_txs:
            store.create_transaction(
                customer_id=customer_ids[cname],
                customer_name=cname,
                amount=amount,
                tx_type=tx_type,
                transaction_date=tx_date,
                note=note,
                confidence=1.0,
                audit_action="seed",
                audit_reason="synthetic_demo_data",
                audit_new_value={"amount": amount, "type": tx_type},
                created_at=now_iso,
            )

        store.ensure_default_settings(
            [
                ("preferred_language", "hi-IN"),
                ("ai_mode", "cascaded"),
                ("speech_provider", "auto"),
                ("ephemeral_transcripts", "false"),
                ("dpdp_consent_accepted", "false"),
            ]
        )

