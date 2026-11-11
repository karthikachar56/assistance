"""Multilingual kirana NLU benchmark, phonetic matcher, WebSocket & latency persistence suite."""

from __future__ import annotations

import time
from datetime import date, timedelta

from app.schemas.models import LatencyBreakdown
from app.services.customer_matcher import resolve_customer
from app.services.fast_path import try_fast_path


def test_indic_phonetic_and_honorific_customer_matching(isolated_container):
    with isolated_container.db.connect() as conn:
        # Honorific stripping
        m1 = resolve_customer(conn, "Suresh bhaiya")
        assert m1.status == "single"
        assert m1.matched["name"] == "Suresh Patel"

        # Kannada/Hindi phonetic transliteration variations
        m2 = resolve_customer(conn, "Laxmi Devi")
        assert m2.status == "single"
        assert m2.matched["name"] == "Lakshmi Devi"

        m3 = resolve_customer(conn, "Laksmi avare")
        assert m3.status == "single"
        assert m3.matched["name"] == "Lakshmi Devi"

        # Nominative -a ending in Kannada still preserves duplicate-name ambiguity guard!
        m4 = resolve_customer(conn, "Ramesha")
        assert m4.status == "ambiguous"
        assert {c["name"] for c in m4.candidates} == {"Ramesh Kumar", "Ramesh Gowda"}


def test_multilingual_kirana_benchmark_accuracy_and_speed(isolated_container):
    known = isolated_container.ledger.list_customer_names()
    today_iso = date.today().isoformat()
    yest_iso = (date.today() - timedelta(days=1)).isoformat()
    parso_iso = (date.today() - timedelta(days=2)).isoformat()

    cases = [
        # (utterance, lang, expected_intent, expected_ops: list[(cust_substr, amt, tx_type, date_iso)])
        ("Suresh bhaiya ka teen sau chaalis udhaar", "hi-IN", "record_transactions", [("Suresh", 340.0, "credit", today_iso)]),
        ("Lakshmi ge nalknoora aivattu haaku", "kn-IN", "record_transactions", [("Lakshmi", 450.0, "credit", today_iso)]),
        ("Suresh ku munnooru kadan, netru 150 kuduthaan", "ta-IN", "record_transactions", [("Suresh", 300.0, "credit", today_iso), ("Suresh", 150.0, "payment", yest_iso)]),
        ("Mahesh gariki 250 appu, parso 100 icharu", "te-IN", "record_transactions", [("Mahesh", 250.0, "credit", today_iso), ("Mahesh", 100.0, "payment", parso_iso)]),
        ("Mohan ka sava sau udhaar, kal dhai sau gpay kiya", "hi-IN", "record_transactions", [("Mohan", 125.0, "credit", today_iso), ("Mohan", 250.0, "payment", yest_iso)]),
        ("Lakshmi Devi 480 kodi", "kn-IN", "record_transactions", [("Lakshmi Devi", 480.0, "credit", today_iso)]),
        ("Suresh ka ३४० udhaar", "hi-IN", "record_transactions", [("Suresh", 340.0, "credit", today_iso)]),
        ("Mohan 1.5k upi payment", "en-IN", "record_transactions", [("Mohan", 1500.0, "payment", today_iso)]),
    ]

    t0 = time.perf_counter()
    for idx, (text, lang, exp_intent, exp_ops) in enumerate(cases):
        plan = try_fast_path(text, lang, None, known)
        assert plan is not None, f"Fast-path missed case: {text}"
        assert plan.intent == exp_intent
        assert len(plan.operations) == len(exp_ops)
        res = isolated_container.ledger.process_turn(
            session_id=f"bench-{idx}",
            plan=plan,
            language=lang,  # type: ignore[arg-type]
            model_used="fast-path-bench",
            latency=LatencyBreakdown(),
        )
        assert res.status == "recorded"
        for tx, (cust_sub, amt, tx_type, exp_date) in zip(res.transactions, exp_ops):
            assert cust_sub.lower() in tx.customer_name.lower()
            assert tx.amount == amt
            assert tx.tx_type == tx_type
            assert tx.transaction_date == exp_date

    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert elapsed_ms < 500, f"8-case multilingual batch exceeded 500ms budget ({elapsed_ms:.1f}ms)"


def test_direction_reversal_spoken_correction(isolated_container):
    known = isolated_container.ledger.list_customer_names()
    p1 = try_fast_path("Suresh ka 300 udhaar", "hi-IN", None, known)
    assert p1 is not None
    r1 = isolated_container.ledger.process_turn("corr-dir", p1, "hi-IN", "fp", LatencyBreakdown())
    assert r1.transactions[0].tx_type == "credit"

    p2 = try_fast_path("Galat ho gaya, udhaar nahi payment tha", "hi-IN", None, known)
    assert p2 is not None
    assert p2.intent == "correction"
    assert p2.corrected_tx_type == "payment"
    r2 = isolated_container.ledger.process_turn("corr-dir", p2, "hi-IN", "fp", LatencyBreakdown())
    assert r2.status == "corrected"
    assert r2.transactions[0].tx_type == "payment"
    assert r2.transactions[0].amount == 300.0


def test_websocket_endpoint_handles_missing_key_cleanly_and_latency_persists(api_client):
    # Rest turn persists actual latency_ms (regression test for latency_ms == 0 bug)
    res = api_client.post(
        "/api/voice/turn",
        json={"session_id": "lat-check", "transcript": "Aaj kitna udhaar gaya?", "language": "hi-IN"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["voice_log_id"] is not None

    history = api_client.get("/api/voice/history").json()
    entry = next(h for h in history if h["id"] == body["voice_log_id"])
    assert entry["latency_ms"] >= 0

    # WebSocket /ws/live in unconfigured key test environment returns structured error frame instead of raw crash
    with api_client.websocket_connect("/ws/live?session_id=ws-test&language=hi-IN") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert msg["recoverable"] is False


def test_jarvis_interactive_confirmation_and_cancel_flow(api_client):
    custs_before = api_client.get("/api/customers?q=Suresh").json()
    bal_before = custs_before[0]["outstanding_balance"]

    # Step 1: Ask to add credit with interactive_confirm=True -> stages pending without writing yet
    r1 = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "jarvis-1",
            "transcript": "Add $340 credit to Suresh Patel",
            "language": "en-IN",
            "interactive_confirm": True,
        },
    )
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1["status"] == "needs_confirmation"
    assert "sure, sir" in b1["spoken_reply"].lower()
    assert b1["pending_action"]["kind"] == "confirmation"
    assert b1["transactions"] == []

    # Balance must remain untouched before user confirms
    bal_mid = api_client.get("/api/customers?q=Suresh").json()[0]["outstanding_balance"]
    assert bal_mid == bal_before

    # Step 2: Confirm with natural "Yes sir" -> commits atomically
    r2 = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "jarvis-1",
            "transcript": "Yes sir",
            "language": "en-IN",
            "interactive_confirm": True,
        },
    )
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2["status"] == "recorded"
    assert len(b2["transactions"]) == 1
    assert b2["transactions"][0]["amount"] == 340.0
    assert b2["transactions"][0]["tx_type"] == "credit"

    bal_after = api_client.get("/api/customers?q=Suresh").json()[0]["outstanding_balance"]
    assert bal_after == bal_before + 340.0


def test_audio_endpoint_503_graceful_fallback_to_browser_transcript(isolated_container, api_client, monkeypatch):
    monkeypatch.setattr(isolated_container.settings, "gemini_api_key", "fake-test-key")

    def _boom(**kwargs):
        raise RuntimeError("503 gemini-3.8-flash high demand")

    monkeypatch.setattr(isolated_container.gemini, "parse_turn", _boom)

    fake_wav = b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
    res = api_client.post(
        "/api/voice/audio",
        files={"file": ("clip.wav", fake_wav, "audio/wav")},
        data={
            "session_id": "audio-503",
            "language": "en-IN",
            "ai_mode": "cascaded",
            "include_tts": "false",
            "fallback_transcript": "Add 250 payment for Mohan Rao",
            "interactive_confirm": "true",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "needs_confirmation"
    assert "Mohan Rao" in body["spoken_reply"]
    assert "250" in body["spoken_reply"]

    # Confirm via audio fallback cancel
    res2 = api_client.post(
        "/api/voice/audio",
        files={"file": ("clip.wav", fake_wav, "audio/wav")},
        data={
            "session_id": "audio-503",
            "language": "en-IN",
            "ai_mode": "cascaded",
            "include_tts": "false",
            "fallback_transcript": "No, cancel",
            "interactive_confirm": "true",
        },
    )
    assert res2.status_code == 200
    assert res2.json()["status"] == "cancelled"


def test_gemini_engine_model_fallback_cascade(isolated_container, monkeypatch):
    from app.schemas.models import VoiceTurnPlan

    monkeypatch.setattr(isolated_container.settings, "gemini_api_key", "fake-test-key")
    called = []

    def _fake_call(model_id, thinking_level, system_instruction, input_payload):
        called.append(model_id)
        if model_id == "gemini-3.8-flash":
            raise RuntimeError("503 UNAVAILABLE")
        return VoiceTurnPlan(
            transcript="Ramesh ka 340 udhaar",
            intent="query_ledger",
            confidence=0.95,
            detected_language="hi-IN",
        )

    monkeypatch.setattr(isolated_container.gemini, "_call_model", _fake_call)
    plan, used = isolated_container.gemini.parse_turn(
        language="hi-IN",
        known_customers=["Ramesh Kumar"],
        pending_action=None,
        ai_mode="flash",
        transcript="test",
    )
    assert called[0] == "gemini-3.8-flash"
    assert used == isolated_container.settings.gemini_lite_model
    assert plan.confidence == 0.95


def test_indic_unicode_fast_path_kannada_and_hindi(api_client):
    r1 = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "indic-unicode",
            "transcript": "Suresh Patel 340 ಉದಾರ್",
            "language": "kn-IN",
            "ai_mode": "offline",
            "include_tts": False,
            "interactive_confirm": True,
        },
    )
    assert r1.status_code == 200
    assert r1.json()["status"] == "needs_confirmation"
    assert any("\u0c80" <= ch <= "\u0cff" for ch in r1.json()["spoken_reply"])

    r2 = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "indic-unicode",
            "transcript": "ಹೌದು",
            "language": "kn-IN",
            "ai_mode": "offline",
            "include_tts": False,
            "interactive_confirm": True,
        },
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "recorded"
    assert r2.json()["transactions"][0]["amount"] == 340.0
    assert any("\u0c80" <= ch <= "\u0cff" for ch in r2.json()["spoken_reply"])

    for q_kn in (
        "ಇವತ್ತು ಎಷ್ಟು ಉದಾರ್ ಆಗಿದೆ?",
        "ಇವತ್ತು ಯಾರು ಪೇಮೆಂಟ್ ಮಾಡಿದ್ದಾರೆ?",
        "ಅಂಗಡಿಯ ಒಟ್ಟು ಬಾಕಿ ಎಷ್ಟು?",
    ):
        rk = api_client.post(
            "/api/voice/turn",
            json={
                "session_id": "indic-unicode",
                "transcript": q_kn,
                "language": "kn-IN",
                "ai_mode": "offline",
                "include_tts": False,
            },
        )
        assert rk.status_code == 200
        body = rk.json()
        assert body["intent"] == "query_ledger"
        assert any("\u0c80" <= ch <= "\u0cff" for ch in body["spoken_reply"])
        assert "dukaan ka" not in body["spoken_reply"].lower()
        assert "aaj total" not in body["spoken_reply"].lower()


def test_voice_auto_navigation_for_customer_details_and_pages(api_client):
    # Total customer details -> speaks summary AND navigates to Customers page
    r_all = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "nav-all-cust",
            "transcript": "Tell me the total customer details",
            "language": "en-IN",
            "ai_mode": "offline",
            "include_tts": False,
        },
    )
    assert r_all.status_code == 200
    b_all = r_all.json()
    assert b_all["status"] == "answered"
    assert b_all["navigate_tab"] == "customers"
    assert b_all["open_customer_details"] is False
    assert "customers" in b_all["spoken_reply"].lower()
    assert "₹" in b_all["spoken_reply"]

    # Specific customer details -> opens customer details inline on Customers tab
    r_cust = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "nav-ramesh-gowda",
            "transcript": "Show Ramesh Gowda details",
            "language": "en-IN",
            "ai_mode": "offline",
            "include_tts": False,
        },
    )
    assert r_cust.status_code == 200
    b_cust = r_cust.json()
    assert b_cust["status"] == "answered"
    assert b_cust["navigate_tab"] == "customers"
    assert b_cust["open_customer_details"] is True
    assert b_cust["highlight_customer_name"] == "Ramesh Gowda"
    assert b_cust["highlight_customer_id"] is not None

    # Specific customer details with 'customer details' phrase -> opens Ramesh Gowda inline on Customers tab
    r_cust2 = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "nav-ramesh-gowda-2",
            "transcript": "Show Ramesh Gowda customer details",
            "language": "en-IN",
            "ai_mode": "offline",
            "include_tts": False,
        },
    )
    assert r_cust2.status_code == 200
    assert r_cust2.json()["navigate_tab"] == "customers"
    assert r_cust2.json()["open_customer_details"] is True
    assert r_cust2.json()["highlight_customer_name"] == "Ramesh Gowda"

    # Open Settings page
    r_settings = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "nav-settings",
            "transcript": "Open settings",
            "language": "en-IN",
            "ai_mode": "offline",
            "include_tts": False,
        },
    )
    assert r_settings.status_code == 200
    assert r_settings.json()["navigate_tab"] == "settings"

    # Open Khata Dashboard page
    r_home = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "nav-home",
            "transcript": "Open dashboard",
            "language": "en-IN",
            "ai_mode": "offline",
            "include_tts": False,
        },
    )
    assert r_home.status_code == 200
    assert r_home.json()["navigate_tab"] == "home"


def test_live_add_credit_and_add_debit_classification(api_client):
    r_cred = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "live-cred-deb",
            "transcript": "Add 200 credit to Ramesh Gowda",
            "language": "en-IN",
            "ai_mode": "offline",
            "include_tts": False,
        },
    )
    assert r_cred.status_code == 200
    b_cred = r_cred.json()
    assert b_cred["status"] == "recorded"
    assert b_cred["transactions"][0]["tx_type"] == "credit"
    assert b_cred["transactions"][0]["amount"] == 200.0

    r_deb = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "live-cred-deb",
            "transcript": "Add 150 debit to Ramesh Gowda",
            "language": "en-IN",
            "ai_mode": "offline",
            "include_tts": False,
        },
    )
    assert r_deb.status_code == 200
    b_deb = r_deb.json()
    assert b_deb["status"] == "recorded"
    assert b_deb["transactions"][0]["tx_type"] == "payment"
    assert b_deb["transactions"][0]["amount"] == 150.0


def test_ai_create_customer_via_api_and_live_tool(api_client, isolated_container):
    from app.services.gemini_live import _execute_live_tool

    # 1. Voice/text turn asks AI to add customer with phone & opt-in -> creates & navigates to Customers
    r1 = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "cust-create-1",
            "transcript": "Add new customer Anand Sharma phone 9876543210 opt in",
            "language": "en-IN",
            "ai_mode": "offline",
            "include_tts": False,
        },
    )
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1["status"] == "recorded"
    assert b1["intent"] == "create_customer"
    assert b1["navigate_tab"] == "customers"
    assert b1["highlight_customer_name"] == "Anand Sharma"
    assert b1["highlight_customer_id"] is not None

    customers = api_client.get("/api/customers").json()
    anand = next(c for c in customers if c["name"] == "Anand Sharma")
    assert anand["phone"] == "9876543210"
    assert anand["reminder_opt_in"] is True

    # 2. Missing name asks user for name, then creates on follow-up turn
    r2 = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "cust-create-2",
            "transcript": "Add new customer",
            "language": "en-IN",
            "ai_mode": "offline",
            "include_tts": False,
        },
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "needs_confirmation"

    r3 = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "cust-create-2",
            "transcript": "Priya Nair",
            "language": "en-IN",
            "ai_mode": "offline",
            "include_tts": False,
        },
    )
    assert r3.status_code == 200
    assert r3.json()["status"] == "recorded"
    assert r3.json()["navigate_tab"] == "customers"
    assert r3.json()["highlight_customer_name"] == "Priya Nair"

    # 3. Gemini Live tool execution directly creates customer too
    live_res = _execute_live_tool(
        ledger=isolated_container.ledger,
        session_id="live-cust-tool",
        language="en-IN",
        model_used="gemini-3.8-live",
        tool_name="create_khata_customer",
        args={"transcript": "Create customer Kiran Verma", "customer_name": "Kiran Verma"},
    )
    assert live_res.status == "recorded"
    assert live_res.navigate_tab == "customers"
    assert live_res.highlight_customer_name == "Kiran Verma"


def test_ai_edit_customer_and_manual_only_delete_guard(api_client, isolated_container):
    from app.services.gemini_live import _execute_live_tool

    # Seed customer via AI first
    r_add = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "cust-edit-1",
            "transcript": "Add customer Anand Sharma",
            "language": "en-IN",
            "ai_mode": "offline",
            "include_tts": False,
        },
    )
    assert r_add.status_code == 200
    cid = r_add.json()["highlight_customer_id"]
    assert cid is not None

    # 1. AI edits phone, language, reminder opt-in, and name
    r_edit_phone = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "cust-edit-1",
            "transcript": "Edit customer Anand Sharma phone 9988776655 opt in",
            "language": "en-IN",
            "ai_mode": "offline",
            "include_tts": False,
        },
    )
    assert r_edit_phone.status_code == 200
    b_ep = r_edit_phone.json()
    assert b_ep["status"] == "recorded"
    assert b_ep["intent"] == "update_customer"
    assert b_ep["navigate_tab"] == "customers"
    assert b_ep["open_customer_details"] is True

    r_rename = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "cust-edit-1",
            "transcript": "Rename Anand Sharma to Anand Rao language Kannada",
            "language": "en-IN",
            "ai_mode": "offline",
            "include_tts": False,
        },
    )
    assert r_rename.status_code == 200
    assert r_rename.json()["highlight_customer_name"] == "Anand Rao"

    anand = next(c for c in api_client.get("/api/customers").json() if c["id"] == cid)
    assert anand["name"] == "Anand Rao"
    assert anand["phone"] == "9988776655"
    assert anand["preferred_language"] == "kn-IN"
    assert anand["reminder_opt_in"] is True

    # 2. Live tool `update_khata_customer` edits customer details too
    live_upd = _execute_live_tool(
        ledger=isolated_container.ledger,
        session_id="live-edit-tool",
        language="en-IN",
        model_used="gemini-3.8-live",
        tool_name="update_khata_customer",
        args={
            "transcript": "Update Anand Rao phone 9000011111",
            "customer_name": "Anand Rao",
            "phone": "9000011111",
        },
    )
    assert live_upd.status == "recorded"
    anand2 = next(c for c in api_client.get("/api/customers").json() if c["id"] == cid)
    assert anand2["phone"] == "9000011111"

    # 3. AI MUST REFUSE delete requests (manual-only delete policy)
    r_del_ai = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "cust-edit-1",
            "transcript": "Delete customer Anand Rao",
            "language": "en-IN",
            "ai_mode": "offline",
            "include_tts": False,
        },
    )
    assert r_del_ai.status_code == 200
    assert r_del_ai.json()["intent"] == "delete_customer_blocked"
    assert "manually" in r_del_ai.json()["spoken_reply"].lower()
    # Customer still exists after blocked AI delete!
    assert any(c["id"] == cid for c in api_client.get("/api/customers").json())

    # 4. Manual REST DELETE works and cleans up customer + returns 404 thereafter
    r_del_manual = api_client.delete(f"/api/customers/{cid}")
    assert r_del_manual.status_code == 200
    assert r_del_manual.json()["status"] == "deleted"
    assert not any(c["id"] == cid for c in api_client.get("/api/customers").json())
    assert api_client.delete(f"/api/customers/{cid}").status_code == 404


def test_today_credit_and_payment_customer_breakdown(api_client, isolated_container):
    from app.services.gemini_live import _execute_live_tool

    # Record additional credit & payment today for Ramesh Gowda on top of Mahesh Verma's seeded today credit
    r_rec = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "today-brk-1",
            "transcript": "Add 200 credit to Ramesh Gowda",
            "language": "en-IN",
            "ai_mode": "offline",
            "include_tts": False,
        },
    )
    assert r_rec.status_code == 200

    dash = api_client.get("/api/dashboard").json()
    today_cred_names = [item["customer_name"] for item in dash["today_credit_customers"]]
    assert "Ramesh Gowda" in today_cred_names
    assert "Mahesh Verma" in today_cred_names
    assert len(dash["outstanding_customers"]) >= 2

    for prompt in (
        "Who took credit today? List the individual customer names",
        "List today's credit customer names",
        "Only today",
    ):
        q_resp = api_client.post(
            "/api/voice/turn",
            json={
                "session_id": "today-brk-1",
                "transcript": prompt,
                "language": "en-IN",
                "ai_mode": "offline",
                "include_tts": False,
            },
        )
        assert q_resp.status_code == 200
        body = q_resp.json()
        assert body["intent"] == "query_ledger"
        assert "Ramesh Gowda" in body["spoken_reply"]
        assert "Mahesh Verma" in body["spoken_reply"]
        assert len(body["transactions"]) >= 2
        assert {item["customer_name"] for item in body["customer_breakdown"]} >= {"Ramesh Gowda", "Mahesh Verma"}

    live_res = _execute_live_tool(
        ledger=isolated_container.ledger,
        session_id="live-today-brk",
        language="en-IN",
        model_used="gemini-3.8-live",
        tool_name="query_khata_ledger",
        args={"transcript": "Who took credit today?", "kind": "credit_sum_for_date", "date_phrase": "today"},
    )
    assert "Ramesh Gowda" in live_res.spoken_reply
    assert "Mahesh Verma" in live_res.spoken_reply
    assert {item.customer_name for item in live_res.customer_breakdown} >= {"Ramesh Gowda", "Mahesh Verma"}

    r_pay = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "today-brk-1",
            "transcript": "Who paid today?",
            "language": "en-IN",
            "ai_mode": "offline",
            "include_tts": False,
        },
    )
    assert r_pay.status_code == 200
    assert r_pay.json()["intent"] == "query_ledger"
    assert any(item["customer_name"] == "Mohan Rao" for item in r_pay.json()["customer_breakdown"])







