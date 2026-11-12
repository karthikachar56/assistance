"""End-to-end FastAPI TestClient integration tests including opt-in reminder enforcement & latency."""


def test_end_to_end_voice_turn_and_latency(api_client):
    res = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "judge-demo",
            "transcript": "Suresh ka teen sau chaalis udhaar",
            "language": "hi-IN",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "recorded"
    assert body["transactions"][0]["amount"] == 340.0
    assert body["ai_disclosure_prepended"] is True
    assert body["latency"]["total_ms"] < 2000


def test_opt_in_reminder_guardrail_blocks_non_opted_in_customer(api_client):
    customers = api_client.get("/api/customers").json()
    suresh = next(c for c in customers if c["name"] == "Suresh Patel")
    assert suresh["reminder_opt_in"] is False

    blocked = api_client.post("/api/reminders/send", json={"customer_id": suresh["id"], "language": "hi-IN"})
    assert blocked.status_code == 403

    ramesh = next(c for c in customers if c["name"] == "Ramesh Kumar")
    allowed = api_client.post("/api/reminders/send", json={"customer_id": ramesh["id"], "language": "hi-IN"})
    assert allowed.status_code == 200
    assert allowed.json()["sent"] is True


def test_dpdp_wipe_and_csv_export(api_client):
    api_client.post(
        "/api/voice/turn",
        json={"session_id": "wipe-test", "transcript": "Aaj kitna udhaar gaya?", "language": "hi-IN"},
    )
    hist_before = api_client.get("/api/voice/history").json()
    assert len(hist_before) >= 1

    wipe = api_client.delete("/api/privacy/transcripts")
    assert wipe.status_code == 200
    assert wipe.json()["status"] == "wiped"
    assert api_client.get("/api/voice/history").json() == []

    csv_res = api_client.get("/api/export/csv")
    assert csv_res.status_code == 200
    assert "Customer,Date,Type,Amount (INR)" in csv_res.text


def test_vercel_serverless_entrypoint_and_readonly_safety(monkeypatch, tmp_path, api_client):
    import importlib
    import tempfile
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app import main as main_mod
    from app.config import Settings
    from app.db import crypto as crypto_mod

    monkeypatch.setenv("VERCEL", "1")
    tmp_db = Settings(ledger_encryption_key="vercel-test-key", mongodb_url="").resolved_database_path
    assert Path(tmp_db).parent == Path(tempfile.gettempdir())

    # Simulate read-only repo directory where _KEY_FILE cannot be written
    monkeypatch.setattr(crypto_mod, "_KEY_FILE", tmp_path / "missing_ro_dir" / ".ledger.key")
    monkeypatch.setattr(crypto_mod, "_TMP_KEY_FILE", tmp_path / "tmp_vercel_ledger.key")
    cipher_vercel = crypto_mod.FieldCipher("")
    cipher_other = crypto_mod.FieldCipher("completely-different-local-key")
    foreign_cipher_token = cipher_other.encrypt("+91-9876543210")
    assert cipher_vercel.decrypt(foreign_cipher_token) is None

    # Verify backend/app/main.py loads FastAPI cleanly and reports live_ws_supported=False on Vercel
    reloaded = importlib.reload(main_mod)
    with TestClient(reloaded.app) as client:
        assert client.get("/health").json() == {"status": "healthy"}
        status_body = client.get("/api/status").json()
        assert status_body["status"] == "ok"
        assert status_body["live_ws_supported"] is False


def test_stateless_pending_action_and_cancel_route_without_pending(api_client):
    # Turn 1 creates a pending confirmation in session A
    r1 = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "vercel-session-a",
            "transcript": "Suresh Patel 320 udhaar",
            "language": "hi-IN",
            "interactive_confirm": True,
            "include_tts": False,
        },
    )
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1["status"] == "needs_confirmation"
    assert b1["pending_action"]["kind"] == "confirmation"

    # Turn 2 simulates routing to a different cold serverless container (session B has no DB pending state)
    r2 = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "vercel-session-b",
            "transcript": "No, cancel",
            "language": "hi-IN",
            "interactive_confirm": True,
            "include_tts": False,
            "pending_action": b1["pending_action"],
        },
    )
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2["status"] == "cancelled"
    assert b2["pending_action"] is None

    # Turn 3 simulates clicking Cancel when there is no pending action anywhere
    r3 = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "vercel-session-b",
            "transcript": "No, cancel",
            "language": "hi-IN",
            "interactive_confirm": True,
            "include_tts": False,
        },
    )
    assert r3.status_code == 200
    b3 = r3.json()
    assert b3["status"] == "cancelled"
    assert "samajh nahi aaya" not in b3["spoken_reply"].lower()


def test_conversational_checks_and_multi_customer_query(api_client):
    r_hear = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "conv-1",
            "transcript": "hello, main jo bol raha hoon, kya tumhe sunai de raha hai?",
            "language": "hi-IN",
            "include_tts": False,
        },
    )
    assert r_hear.status_code == 200
    body_hear = r_hear.json()
    assert body_hear["intent"] == "greeting"
    assert "awaaz" in body_hear["spoken_reply"].lower()

    r_cap = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "conv-1",
            "transcript": "Hello, explain briefly what you can do",
            "language": "en-IN",
            "include_tts": False,
        },
    )
    assert r_cap.status_code == 200
    assert "credit" in r_cap.json()["spoken_reply"].lower()

    r_multi = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "conv-1",
            "transcript": "Mohan Rao aur Suresh Patel ke pichhle transactions batao",
            "language": "hi-IN",
            "include_tts": False,
        },
    )
    assert r_multi.status_code == 200
    multi_body = r_multi.json()
    assert multi_body["intent"] == "query_ledger"
    assert "Mohan Rao" in multi_body["spoken_reply"]
    assert "Suresh Patel" in multi_body["spoken_reply"]
    assert multi_body["open_customer_details"] is False


def test_auto_speech_provider_synthesizes_gemini_tts_and_live_tool_route(isolated_container, api_client, monkeypatch):
    monkeypatch.setattr(isolated_container.settings, "gemini_api_key", "fake-test-key")
    monkeypatch.setattr(
        isolated_container.gemini,
        "synthesize_tts",
        lambda text, lang: ("AQIDBA==", "audio/wav"),
    )

    # speech_provider starts as "auto" -> should now return Gemini WAV audio cleanly
    res = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "tts-auto",
            "transcript": "Aaj kitna udhaar gaya?",
            "language": "hi-IN",
            "include_tts": True,
        },
    )
    assert res.status_code == 200
    assert res.json()["audio_base64"] == "AQIDBA=="
    assert res.json()["audio_mime_type"] == "audio/wav"

    # Explicit "browser" skips Cloud TTS
    api_client.patch("/api/settings", json={"speech_provider": "browser"})
    res_browser = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "tts-auto",
            "transcript": "Aaj kitna udhaar gaya?",
            "language": "hi-IN",
            "include_tts": True,
        },
    )
    assert res_browser.status_code == 200
    assert res_browser.json()["audio_base64"] is None

    # /api/status advertises live_ephemeral_supported when Gemini key exists
    st = api_client.get("/api/status").json()
    assert st["live_ephemeral_supported"] is True

    # /api/voice/live-tool supports interactive confirm -> stateless cancel round-trip
    lt1 = api_client.post(
        "/api/voice/live-tool",
        json={
            "session_id": "ephem-s1",
            "language": "en-IN",
            "tool_name": "record_khata_operations",
            "interactive_confirm": True,
            "args": {
                "transcript": "Add 200 credit to Suresh Patel",
                "operations": [{"customer_name": "Suresh Patel", "amount": 200, "tx_type": "credit"}],
            },
        },
    )
    assert lt1.status_code == 200
    assert lt1.json()["status"] == "needs_confirmation"

    lt2 = api_client.post(
        "/api/voice/live-tool",
        json={
            "session_id": "ephem-s2",
            "language": "en-IN",
            "tool_name": "confirm_pending_action",
            "interactive_confirm": True,
            "pending_action": lt1.json()["pending_action"],
            "args": {"transcript": "No, cancel", "confirmed": False},
        },
    )
    assert lt2.status_code == 200
    assert lt2.json()["status"] == "cancelled"


def test_gemini_engine_cooldown_and_mongo_fallback_healing(isolated_container, monkeypatch, tmp_path):
    from app.config import Settings
    from app import deps as deps_mod
    from app.schemas.models import VoiceTurnPlan

    monkeypatch.setattr(isolated_container.settings, "gemini_api_key", "fake-test-key")
    calls: list[str] = []

    def _fake_call(model_id, thinking_level, system_instruction, input_payload):
        calls.append(model_id)
        if model_id == "gemini-3.8-flash":
            raise RuntimeError("429 RateLimitError limit: 20 requests per day")
        return VoiceTurnPlan(
            transcript="hello",
            intent="greeting",
            clarification_question="Yes sir, listening clearly!",
            confidence=0.95,
            detected_language="en-IN",
        )

    monkeypatch.setattr(isolated_container.gemini, "_call_model", _fake_call)
    p1, u1 = isolated_container.gemini.parse_turn(
        language="en-IN", known_customers=[], pending_action=None, ai_mode="cascaded", transcript="hello"
    )
    assert u1 == isolated_container.settings.gemini_lite_model
    assert p1.clarification_question == "Yes sir, listening clearly!"
    assert calls == ["gemini-3.8-flash", isolated_container.settings.gemini_lite_model]

    calls.clear()
    _, u2 = isolated_container.gemini.parse_turn(
        language="en-IN", known_customers=[], pending_action=None, ai_mode="cascaded", transcript="hello again"
    )
    assert u2 == isolated_container.settings.gemini_lite_model
    # Cooled down 3.8 Flash was skipped on turn 2!
    assert calls == [isolated_container.settings.gemini_lite_model]

    # Verify deps.get_container() heals out of SQLite fallback once MongoDB recovers
    attempts = {"n": 0}

    class _MockMongoStore:
        backend_name = "mongodb"
        database_label = "voice_khata"

        def __init__(self, mongo_url="", db_name="voice_khata"):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("TLSV1_ALERT_INTERNAL_ERROR")

        def unit_of_work(self):
            from contextlib import nullcontext
            return nullcontext()

        def count_customers(self):
            return 1

        def close(self):
            pass

    monkeypatch.setattr(deps_mod, "MongoKhataStore", _MockMongoStore)
    cfg = Settings(
        gemini_api_key="",
        gnani_api_key="",
        mongodb_url="mongodb+srv://user:pass@cluster.example.net/voice_khata",
        database_path=str(tmp_path / "heal_fallback.db"),
        ledger_encryption_key="test-key-heal",
    )
    degraded = deps_mod.build_container(settings=cfg)
    assert degraded.storage_fallback_reason is not None
    deps_mod.set_container(degraded)
    deps_mod._mongo_retry_after = 0.0
    healed = deps_mod.get_container()
    assert healed.storage_fallback_reason is None
    assert healed.db.backend_name == "mongodb"
    deps_mod.set_container(None)


def test_all_transactions_endpoint_and_ledgers_navigation(api_client):
    customers = api_client.get("/api/customers").json()
    all_txs = api_client.get("/api/transactions").json()
    assert len(all_txs) >= len(customers)
    assert all(tx["status"] != "voided" for tx in all_txs)
    customer_ids_with_txs = {tx["customer_id"] for tx in all_txs}
    assert len(customer_ids_with_txs) >= 2

    target_cust = customers[0]
    filtered = api_client.get(f"/api/transactions?customer_id={target_cust['id']}").json()
    per_cust = api_client.get(f"/api/customers/{target_cust['id']}/transactions").json()
    assert filtered == per_cust

    nav = api_client.post(
        "/api/voice/turn",
        json={
            "session_id": "nav-ledgers",
            "transcript": "Open customer ledgers",
            "language": "en-IN",
            "ai_mode": "offline",
            "include_tts": False,
        },
    )
    assert nav.status_code == 200
    assert nav.json()["navigate_tab"] == "ledgers"


def test_user_signup_login_session_and_encryption(api_client, isolated_container):
    signup = api_client.post(
        "/api/auth/signup",
        json={
            "name": "Karthik Achar",
            "shop_name": "Achar Kirana",
            "email": "Karthik@Example.com",
            "phone": "+91-9876500001",
            "password": "secret-pass-123",
            "preferred_language": "kn-IN",
        },
    )
    assert signup.status_code == 201
    s_body = signup.json()
    assert s_body["token_type"] == "bearer"
    assert s_body["access_token"].startswith("vk1.")
    assert s_body["user"]["email"] == "karthik@example.com"
    assert s_body["user"]["shop_name"] == "Achar Kirana"
    assert s_body["user"]["phone"] == "+91-9876500001"
    assert "password_hash" not in s_body["user"]

    raw_user = isolated_container.db.get_user_by_email("karthik@example.com")
    assert raw_user is not None
    assert raw_user["phone_enc"] != "+91-9876500001"
    assert raw_user["password_hash"].startswith("scrypt$")
    assert "secret-pass-123" not in raw_user["password_hash"]

    dup = api_client.post(
        "/api/auth/signup",
        json={
            "name": "Other User",
            "email": "karthik@example.com",
            "password": "another-password",
        },
    )
    assert dup.status_code == 409

    assert api_client.get("/api/auth/me").status_code == 401
    assert api_client.get("/api/auth/me", headers={"Authorization": "Bearer vk1.bad.sig"}).status_code == 401
    me = api_client.get("/api/auth/me", headers={"Authorization": f"Bearer {s_body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["name"] == "Karthik Achar"

    bad_login = api_client.post(
        "/api/auth/login",
        json={"email": "karthik@example.com", "password": "wrong-password"},
    )
    assert bad_login.status_code == 401

    good_login = api_client.post(
        "/api/auth/login",
        json={"email": "KARTHIK@example.com", "password": "secret-pass-123"},
    )
    assert good_login.status_code == 200
    assert good_login.json()["user"]["id"] == s_body["user"]["id"]
    assert api_client.post("/api/auth/logout").json() == {"status": "logged_out"}


def test_two_accounts_have_isolated_databases_and_calculations(api_client):
    acc_a = api_client.post(
        "/api/auth/signup",
        json={
            "name": "Shopkeeper One",
            "shop_name": "One Provisions",
            "email": "one@example.com",
            "password": "password-one",
            "preferred_language": "hi-IN",
        },
    ).json()
    acc_b = api_client.post(
        "/api/auth/signup",
        json={
            "name": "Shopkeeper Two",
            "shop_name": "Two Supermart",
            "email": "two@example.com",
            "password": "password-two",
            "preferred_language": "kn-IN",
        },
    ).json()

    h_a = {"Authorization": f"Bearer {acc_a['access_token']}"}
    h_b = {"Authorization": f"Bearer {acc_b['access_token']}"}

    # Each account begins with a completely clean, separate database
    st_a = api_client.get("/api/status", headers=h_a).json()
    st_b = api_client.get("/api/status", headers=h_b).json()
    assert st_a["database_name"] != st_b["database_name"]
    assert st_a["settings"]["preferred_language"] == "hi-IN"
    assert st_b["settings"]["preferred_language"] == "kn-IN"
    assert api_client.get("/api/customers", headers=h_a).json() == []
    assert api_client.get("/api/customers", headers=h_b).json() == []
    assert api_client.get("/api/dashboard", headers=h_a).json()["total_outstanding"] == 0.0
    assert api_client.get("/api/dashboard", headers=h_b).json()["total_outstanding"] == 0.0

    # Account A records customers, transactions, voice history & settings
    turn_a = api_client.post(
        "/api/voice/turn",
        headers=h_a,
        json={"session_id": "sess-a", "transcript": "Vikram ka 500 udhaar", "language": "hi-IN", "include_tts": False},
    )
    assert turn_a.status_code == 200
    assert turn_a.json()["status"] == "recorded"
    api_client.patch("/api/settings", headers=h_a, json={"ai_mode": "offline"})

    dash_a = api_client.get("/api/dashboard", headers=h_a).json()
    assert dash_a["total_customers"] == 1
    assert dash_a["total_outstanding"] == 500.0
    assert dash_a["today_credit"] == 500.0
    assert "Vikram" in api_client.get("/api/export/csv", headers=h_a).text

    # Account B remains untouched: 0 customers, Rs 0 totals, empty voice logs, original settings
    dash_b = api_client.get("/api/dashboard", headers=h_b).json()
    assert dash_b["total_customers"] == 0
    assert dash_b["total_outstanding"] == 0.0
    assert dash_b["today_credit"] == 0.0
    assert api_client.get("/api/customers", headers=h_b).json() == []
    assert api_client.get("/api/voice/history", headers=h_b).json() == []
    assert api_client.get("/api/settings", headers=h_b).json()["ai_mode"] == "cascaded"
    assert "Vikram" not in api_client.get("/api/export/csv", headers=h_b).text

    # Account B creates its own dataset and calculations independently
    cust_b = api_client.post("/api/customers", headers=h_b, json={"name": "Anita"}).json()
    api_client.post(
        "/api/transactions",
        headers=h_b,
        json={"customer_id": cust_b["id"], "amount": 1200, "tx_type": "credit"},
    )
    api_client.post(
        "/api/transactions",
        headers=h_b,
        json={"customer_id": cust_b["id"], "amount": 200, "tx_type": "payment"},
    )
    dash_b_after = api_client.get("/api/dashboard", headers=h_b).json()
    assert dash_b_after["total_customers"] == 1
    assert dash_b_after["total_outstanding"] == 1000.0
    assert api_client.get("/api/dashboard", headers=h_a).json()["total_outstanding"] == 500.0

    # Invalid bearer tokens are rejected on scoped routes
    assert api_client.get("/api/customers", headers={"Authorization": "Bearer vk1.invalid.sig"}).status_code == 401


def test_serverless_cross_container_token_and_fallback_503(monkeypatch, tmp_path):
    from fastapi import HTTPException

    from app import config as config_mod
    from app.db import crypto as crypto_mod
    from app.deps import build_container, get_container
    from app.routers.auth import resolve_user_from_token

    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("LEDGER_ENCRYPTION_KEY", "")
    monkeypatch.setenv("MONGODB_URL", "mongodb+srv://demo:pass@cluster.example.net/voice_khata")
    config_mod.get_settings.cache_clear()

    try:
        monkeypatch.setattr(crypto_mod, "_KEY_FILE", tmp_path / "missing_ro" / ".ledger.key")
        monkeypatch.setattr(crypto_mod, "_TMP_KEY_FILE", tmp_path / "container_a" / "ledger.key")
        cipher_a = crypto_mod.FieldCipher("")
        token = cipher_a.sign_token({"sub": 1, "email": "owner@example.com"})

        # Simulate a second cold serverless container with a fresh /tmp path
        monkeypatch.setattr(crypto_mod, "_TMP_KEY_FILE", tmp_path / "container_b" / "ledger.key")
        cipher_b = crypto_mod.FieldCipher("")
        claims = cipher_b.verify_token(token)
        assert claims is not None
        assert claims["sub"] == 1

        # Simulate transient MongoDB fallback: missing user in SQLite fallback must return 503, not 401
        container = get_container()
        fallback_container = build_container(
            settings=config_mod.Settings(
                ledger_encryption_key="test-secret",
                mongodb_url="mongodb+srv://demo:pass@cluster.example.net/voice_khata",
            ),
            db_path=str(tmp_path / "fallback.db"),
        )
        fallback_container.storage_fallback_reason = "MongoDB temporarily unreachable"
        tok = fallback_container.cipher.sign_token({"sub": 999, "email": "missing@example.com"})
        try:
            resolve_user_from_token(tok, fallback_container)
            raise AssertionError("Expected HTTPException")
        except HTTPException as exc:
            assert exc.status_code == 503
        assert container is not None
    finally:
        config_mod.get_settings.cache_clear()





