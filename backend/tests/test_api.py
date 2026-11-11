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

