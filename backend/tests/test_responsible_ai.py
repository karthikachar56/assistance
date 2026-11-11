"""Tests for Responsible AI guardrails, AES-256-GCM encryption, and DPDP wipe."""

import pytest

from app.services.responsible_ai import build_polite_reminder, get_ai_disclosure, wipe_demo_transcripts


def test_first_turn_ai_disclosure_languages():
    assert "AI" in get_ai_disclosure("hi-IN")
    assert "AI" in get_ai_disclosure("kn-IN")
    assert "AI" in get_ai_disclosure("en-IN")


def test_polite_reminder_has_no_pressure_or_lending_words():
    msg = build_polite_reminder("Ramesh Kumar", 340, "hi-IN")
    assert "₹340" in msg
    for banned in ("threat", "police", "court", "penalty", "interest", "loan"):
        assert banned not in msg.lower()


def test_encryption_at_rest_and_dpdp_wipe(isolated_container):
    # Verify raw database rows store encrypted ciphertext, not plaintext phone numbers
    with isolated_container.db.connect() as conn:
        row = conn.execute("SELECT phone_enc FROM customers WHERE name = 'Ramesh Kumar'").fetchone()
    assert row["phone_enc"].startswith("enc:v1:")
    assert "+91-9000000001" not in row["phone_enc"]
    assert isolated_container.cipher.decrypt(row["phone_enc"]) == "+91-9000000001"

    # Verify DPDP wipe clears voice_logs completely
    with isolated_container.db.transaction() as conn:
        conn.execute(
            "INSERT INTO voice_logs (session_id, channel, utterance_enc, parsed_json, ai_reply_enc, model_used, latency_ms, created_at) "
            "VALUES ('s1', 'voice', ?, '{}', ?, 'test', 10, '2026-11-11T00:00:00Z')",
            (isolated_container.cipher.encrypt("Ramesh ka 340 udhaar"), isolated_container.cipher.encrypt("Done")),
        )
        deleted = wipe_demo_transcripts(conn)
        remaining = conn.execute("SELECT COUNT(*) FROM voice_logs").fetchone()[0]
    assert deleted == 1
    assert remaining == 0

