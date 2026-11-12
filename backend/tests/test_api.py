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


