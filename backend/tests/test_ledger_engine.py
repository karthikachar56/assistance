"""Tests for core Voice Khata ledger logic, corrections, disambiguation, and queries."""

from datetime import date, timedelta

from app.schemas.models import LatencyBreakdown
from app.services.fast_path import try_fast_path


def _run_text(container, session_id: str, text: str, lang: str = "hi-IN"):
    pending = container.ledger.get_pending_action(session_id)
    known = container.ledger.list_customer_names()
    plan = try_fast_path(text, lang, pending, known)
    assert plan is not None, f"Fast-path failed to parse: {text}"
    return container.ledger.process_turn(
        session_id=session_id,
        plan=plan,
        language=lang,
        model_used="fast-path-test",
        latency=LatencyBreakdown(),
    )


def test_credit_and_payment_direction_updates_balance(isolated_container):
    # Suresh Patel starts in seed with 850 credit - 200 payment = 650 balance
    res1 = _run_text(isolated_container, "s1", "Suresh ka 340 udhaar")
    assert res1.status == "recorded"
    assert res1.ai_disclosure_prepended is True
    assert res1.transactions[0].amount == 340
    assert res1.transactions[0].tx_type == "credit"

    res2 = _run_text(isolated_container, "s1", "Suresh ne 200 diya")
    assert res2.status == "recorded"
    assert res2.ai_disclosure_prepended is False
    assert res2.transactions[0].tx_type == "payment"

    # Net balance: 650 + 340 - 200 = 790
    with isolated_container.db.connect() as conn:
        cid = res1.transactions[0].customer_id
        bal = isolated_container.ledger._compute_customer_balance(conn, cid)
    assert bal == 790.0


def test_multi_clause_udhaar_and_kal_payment(isolated_container):
    res = _run_text(isolated_container, "s2", "Suresh ka 340 udhaar, kal 200 diya tha")
    assert res.status == "recorded"
    assert len(res.transactions) == 2
    assert res.transactions[0].tx_type == "credit"
    assert res.transactions[0].amount == 340
    assert res.transactions[0].transaction_date == date.today().isoformat()

    assert res.transactions[1].tx_type == "payment"
    assert res.transactions[1].amount == 200
    assert res.transactions[1].transaction_date == (date.today() - timedelta(days=1)).isoformat()


def test_spoken_correction_updates_without_double_counting(isolated_container):
    # Mahesh Verma starts at 150 credit
    first = _run_text(isolated_container, "s3", "Mahesh ka 340 udhaar")
    assert first.status == "recorded"
    cid = first.transactions[0].customer_id

    corrected = _run_text(isolated_container, "s3", "Nahi, 430")
    assert corrected.status == "corrected"
    assert corrected.transactions[0].amount == 430.0

    with isolated_container.db.connect() as conn:
        bal = isolated_container.ledger._compute_customer_balance(conn, cid)
        audits = conn.execute(
            "SELECT action FROM audit_logs WHERE transaction_id = ? ORDER BY id ASC",
            (first.transactions[0].id,),
        ).fetchall()
    assert bal == 580.0  # 150 seed + 430 corrected (NOT 150 + 340 + 430)
    assert [a["action"] for a in audits] == ["create", "correct"]


def test_duplicate_customer_disambiguation_flow(isolated_container):
    res1 = _run_text(isolated_container, "s4", "Ramesh ka 300 udhaar")
    assert res1.status == "needs_disambiguation"
    assert "Ramesh Kumar" in res1.candidate_customers
    assert "Ramesh Gowda" in res1.candidate_customers

    res2 = _run_text(isolated_container, "s4", "Ramesh Gowda")
    assert res2.status == "recorded"
    assert res2.transactions[0].customer_name == "Ramesh Gowda"
    assert res2.transactions[0].amount == 300.0


def test_correction_during_pending_disambiguation_preserves_question(isolated_container):
    res1 = _run_text(isolated_container, "s4b", "Ramesh ka 300 udhaar")
    assert res1.status == "needs_disambiguation"

    res2 = _run_text(isolated_container, "s4b", "Nahi, 430")
    assert res2.status == "needs_disambiguation"
    assert "430" in res2.spoken_reply

    res3 = _run_text(isolated_container, "s4b", "Ramesh Gowda")
    assert res3.status == "recorded"
    assert res3.transactions[0].customer_name == "Ramesh Gowda"
    assert res3.transactions[0].amount == 430.0


def test_ambiguous_direction_asks_clarification(isolated_container):
    res = _run_text(isolated_container, "s5", "Mahesh ko 300 dena hai")
    assert res.status == "needs_confirmation"

    confirm = _run_text(isolated_container, "s5", "haan")
    assert confirm.status == "recorded"
    assert confirm.transactions[0].amount == 300.0


def test_ledger_queries_fast_path(isolated_container):
    q1 = _run_text(isolated_container, "s6", "Aaj kitna udhaar gaya?")
    assert q1.status == "answered"
    assert "150" in q1.spoken_reply  # Seed has 150 credit today for Mahesh Verma

    q2 = _run_text(isolated_container, "s6", "Suresh ka total batao")
    assert q2.status == "answered"
    assert "650" in q2.spoken_reply


def test_unknown_greeting_and_cancel_without_pending_never_loop(isolated_container):
    from app.schemas.models import VoiceTurnPlan

    # 1. Unknown utterance with no pending action must NOT show needs_confirmation
    unknown_plan = VoiceTurnPlan(
        transcript="random mumble",
        intent="unknown",
        confidence=0.2,
        detected_language="hi-IN",
    )
    res_unknown = isolated_container.ledger.process_turn(
        session_id="no-loop-1",
        plan=unknown_plan,
        language="hi-IN",
        model_used="test",
        latency=LatencyBreakdown(),
    )
    assert res_unknown.status == "answered"
    assert res_unknown.pending_action is None

    # 2. Clicking Cancel ("No, cancel") even with no pending returns cancelled cleanly
    res_cancel = _run_text(isolated_container, "no-loop-1", "No, cancel")
    assert res_cancel.status == "cancelled"
    assert "samajh nahi aaya" not in res_cancel.spoken_reply.lower()

    # 3. Clicking Yes confirm ("Yes, confirm") with no pending returns answered cleanly
    res_yes = _run_text(isolated_container, "no-loop-1", "Yes, confirm")
    assert res_yes.status == "answered"
    assert "samajh nahi aaya" not in res_yes.spoken_reply.lower()

    # 4. Greeting does not loop into confirmation
    res_greet = _run_text(isolated_container, "no-loop-1", "Namaste")
    assert res_greet.status == "answered"
    assert res_greet.intent == "greeting"

    # 5. Stateless fallback_pending_action confirms transaction on a brand new cold session
    pending_from_turn1 = {
        "kind": "confirmation",
        "operations": [
            {
                "customer_name": "Suresh Patel",
                "amount": 250.0,
                "tx_type": "credit",
                "resolved_date": date.today().isoformat(),
                "note": None,
                "needs_disambiguation": False,
                "resolved_customer_id": None,
            }
        ],
    }
    plan_yes = try_fast_path("Yes, confirm", "en-IN", pending_from_turn1, ["Suresh Patel"])
    assert plan_yes is not None
    res_stateless = isolated_container.ledger.process_turn(
        session_id="cold-instance-session",
        plan=plan_yes,
        language="en-IN",
        model_used="fast-path-test",
        latency=LatencyBreakdown(),
        fallback_pending_action=pending_from_turn1,
    )
    assert res_stateless.status == "recorded"
    assert res_stateless.transactions[0].amount == 250.0
