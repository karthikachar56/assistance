"""End-to-end tests for MongoKhataStore, container wiring, LedgerEngine, and FastAPI routes."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo import DESCENDING, ReturnDocument

from app.config import Settings
from app.db.crypto import FieldCipher
from app.db.seed import seed_synthetic_data
from app.db.store import MongoKhataStore, _resolve_mongo_db_name
from app.deps import AppContainer, build_container, set_container
from app.main import create_app
from app.schemas.models import LatencyBreakdown
from app.services.fast_path import try_fast_path
from app.services.gemini_engine import GeminiKhataEngine
from app.services.gnani_adapter import GnaniSpeechAdapter
from app.services.ledger_engine import LedgerEngine


def _matches_filter(doc: dict, filt: dict) -> bool:
    for key, cond in filt.items():
        val = doc.get(key)
        if isinstance(cond, dict):
            if "$in" in cond and val not in cond["$in"]:
                return False
            if "$ne" in cond and val == cond["$ne"]:
                return False
        elif val != cond:
            return False
    return True


def _apply_projection(doc: dict, projection: dict | None) -> dict:
    out = deepcopy(doc)
    if projection is None:
        return out
    includes = {k for k, v in projection.items() if v}
    if includes:
        return {k: out[k] for k in includes if k in out}
    for k, v in projection.items():
        if not v:
            out.pop(k, None)
    return out


class _FakeCursor:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs

    def sort(self, key_or_list, direction=1):
        specs = key_or_list if isinstance(key_or_list, list) else [(key_or_list, direction)]
        docs = list(self._docs)
        for field, order in reversed(specs):
            reverse = order == DESCENDING or order == -1
            docs.sort(key=lambda d: (d.get(field) is None, d.get(field)), reverse=reverse)
        return _FakeCursor(docs)

    def limit(self, n: int):
        return _FakeCursor(self._docs[:n])

    def __iter__(self):
        return iter(self._docs)


class _FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict] = []
        self.indexes: list = []

    def create_index(self, keys, unique: bool = False):
        self.indexes.append((list(keys), unique))
        return "ok"

    def count_documents(self, filt: dict) -> int:
        return sum(1 for d in self.docs if _matches_filter(d, filt))

    def insert_one(self, doc: dict):
        # Simulate PyMongo in-place _id mutation so _id leakage bugs would be caught
        doc.setdefault("_id", ObjectId())
        self.docs.append(deepcopy(doc))
        return SimpleNamespace(inserted_id=doc["_id"])

    def find_one(self, filt: dict, projection: dict | None = None) -> dict | None:
        for d in self.docs:
            if _matches_filter(d, filt):
                return _apply_projection(d, projection)
        return None

    def find(self, filt: dict, projection: dict | None = None) -> _FakeCursor:
        matched = [_apply_projection(d, projection) for d in self.docs if _matches_filter(d, filt)]
        return _FakeCursor(matched)

    def _apply_update(self, doc: dict, update: dict, inserted: bool = False) -> None:
        if "$set" in update:
            doc.update(deepcopy(update["$set"]))
        if "$inc" in update:
            for k, delta in update["$inc"].items():
                doc[k] = doc.get(k, 0) + delta
        if inserted and "$setOnInsert" in update:
            doc.update(deepcopy(update["$setOnInsert"]))

    def update_one(self, filt: dict, update: dict, upsert: bool = False):
        for d in self.docs:
            if _matches_filter(d, filt):
                self._apply_update(d, update, inserted=False)
                return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            new_doc = {k: v for k, v in filt.items() if not isinstance(v, dict)}
            new_doc.setdefault("_id", ObjectId())
            self._apply_update(new_doc, update, inserted=True)
            self.docs.append(new_doc)
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=new_doc["_id"])
        return SimpleNamespace(matched_count=0, modified_count=0)

    def update_many(self, filt: dict, update: dict):
        count = 0
        for d in self.docs:
            if _matches_filter(d, filt):
                self._apply_update(d, update, inserted=False)
                count += 1
        return SimpleNamespace(matched_count=count, modified_count=count)

    def delete_one(self, filt: dict):
        for i, d in enumerate(self.docs):
            if _matches_filter(d, filt):
                self.docs.pop(i)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    def delete_many(self, filt: dict):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _matches_filter(d, filt)]
        return SimpleNamespace(deleted_count=before - len(self.docs))

    def find_one_and_update(
        self,
        filt: dict,
        update: dict,
        upsert: bool = False,
        return_document=ReturnDocument.AFTER,
    ) -> dict | None:
        target = None
        inserted = False
        for d in self.docs:
            if _matches_filter(d, filt):
                target = d
                break
        if target is None and upsert:
            target = {k: v for k, v in filt.items() if not isinstance(v, dict)}
            target.setdefault("_id", ObjectId())
            self.docs.append(target)
            inserted = True
        if target is None:
            return None
        before = deepcopy(target)
        self._apply_update(target, update, inserted=inserted)
        return deepcopy(target) if return_document == ReturnDocument.AFTER else before


class _FakeMongoDB:
    def __init__(self) -> None:
        self._collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        return self._collections.setdefault(name, _FakeCollection())


class _FakeAdmin:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def command(self, name: str):
        self.commands.append(name)
        return {"ok": 1}


class _FakeMongoClient:
    def __init__(self) -> None:
        self.admin = _FakeAdmin()
        self._dbs: dict[str, _FakeMongoDB] = {}
        self.closed = False

    def __getitem__(self, name: str) -> _FakeMongoDB:
        return self._dbs.setdefault(name, _FakeMongoDB())

    def close(self) -> None:
        self.closed = True


def test_mongo_db_name_resolution():
    assert _resolve_mongo_db_name("mongodb+srv://user:pw@cluster.mongodb.net/my_kirana?retryWrites=true", "voice_khata") == "my_kirana"
    assert _resolve_mongo_db_name("mongodb://localhost:27017", "voice_khata") == "voice_khata"
    assert _resolve_mongo_db_name("mongodb://localhost:27017/ignored", "custom_shop") == "custom_shop"


def test_build_container_selects_mongo_when_configured(monkeypatch):
    fake_client = _FakeMongoClient()
    monkeypatch.setattr("app.db.store.MongoClient", lambda url, **kwargs: fake_client)

    settings = Settings(
        gemini_api_key="",
        gnani_api_key="",
        ledger_encryption_key="mongo-test-secret-key",
        mongodb_url="mongodb+srv://demo:secret@cluster0.mongodb.net/kirana_prod?retryWrites=true",
        mongodb_db_name="voice_khata",
    )
    container = build_container(settings=settings)
    assert isinstance(container.db, MongoKhataStore)
    assert container.db.backend_name == "mongodb"
    assert container.db.database_label == "kirana_prod"
    assert fake_client.admin.commands == ["ping"]
    assert container.db.count_customers() == 6


def test_mongo_store_end_to_end_ledger_encryption_and_api():
    fake_client = _FakeMongoClient()
    store = MongoKhataStore(
        mongo_url="mongodb://localhost:27017/voice_khata_test",
        db_name="voice_khata_test",
        client=fake_client,
    )
    cipher = FieldCipher("mongo-api-encryption-secret")
    seed_synthetic_data(store, cipher)
    # Re-seeding on non-empty DB must be idempotent
    seed_synthetic_data(store, cipher)
    assert store.count_customers() == 6

    # Raw stored phone numbers in MongoDB must be encrypted ciphertext
    raw_ramesh = fake_client["voice_khata_test"]["customers"].find_one({"name": "Ramesh Kumar"})
    assert raw_ramesh is not None
    assert raw_ramesh["phone_enc"].startswith("enc:v1:")
    assert cipher.decrypt(raw_ramesh["phone_enc"]) == "+91-9000000001"

    settings = Settings(
        gemini_api_key="",
        gnani_api_key="",
        ledger_encryption_key="mongo-api-encryption-secret",
        mongodb_url="mongodb://localhost:27017/voice_khata_test",
        mongodb_db_name="voice_khata_test",
    )
    container = AppContainer(
        settings=settings,
        db=store,
        cipher=cipher,
        ledger=LedgerEngine(store, cipher),
        gemini=GeminiKhataEngine(settings),
        gnani=GnaniSpeechAdapter(settings),
    )

    set_container(container)
    try:
        app = create_app()
        with TestClient(app) as client:
            # Verify /api/status safely reports MongoDB without leaking connection credentials
            status = client.get("/api/status").json()
            assert status["database_backend"] == "mongodb"
            assert status["database_name"] == "voice_khata_test"
            assert "mongodb://" not in str(status)

            # Voice turn 1: credit + kal payment
            turn = client.post(
                "/api/voice/turn",
                json={
                    "session_id": "mongo-live-1",
                    "transcript": "Suresh ka 340 udhaar, kal 200 diya tha",
                    "language": "hi-IN",
                },
            )
            assert turn.status_code == 200
            body = turn.json()
            assert body["status"] == "recorded"
            assert len(body["transactions"]) == 2
            assert body["ai_disclosure_prepended"] is True

            # Voice turn 2: spoken correction ("nahi, 430") updates last tx & audit log
            single = client.post(
                "/api/voice/turn",
                json={
                    "session_id": "mongo-live-2",
                    "transcript": "Mahesh ka 340 udhaar",
                    "language": "hi-IN",
                },
            ).json()
            corr = client.post(
                "/api/voice/turn",
                json={
                    "session_id": "mongo-live-2",
                    "transcript": "Nahi, 430",
                    "language": "hi-IN",
                },
            ).json()
            assert corr["status"] == "corrected"
            assert corr["transactions"][0]["amount"] == 430.0
            assert store.get_customer_balance(single["transactions"][0]["customer_id"]) == 580.0

            # Duplicate customer disambiguation flow
            ambig = client.post(
                "/api/voice/turn",
                json={
                    "session_id": "mongo-live-3",
                    "transcript": "Ramesh ka 300 udhaar",
                    "language": "hi-IN",
                },
            ).json()
            assert ambig["status"] == "needs_disambiguation"
            resolved = client.post(
                "/api/voice/turn",
                json={
                    "session_id": "mongo-live-3",
                    "transcript": "Ramesh Gowda",
                    "language": "hi-IN",
                },
            ).json()
            assert resolved["status"] == "recorded"
            assert resolved["transactions"][0]["customer_name"] == "Ramesh Gowda"

            # Customers, dashboard, manual transaction, reminders, settings, CSV export & DPDP wipe
            customers = client.get("/api/customers").json()
            assert any(c["name"] == "Ramesh Kumar" and c["phone"] == "+91-9000000001" for c in customers)
            new_cust = client.post(
                "/api/customers",
                json={"name": "Kiran Shetty", "phone": "+91-9888888888", "reminder_opt_in": True, "preferred_language": "kn-IN"},
            )
            assert new_cust.status_code == 201
            kiran_id = new_cust.json()["id"]
            assert client.patch(f"/api/customers/{kiran_id}", json={"preferred_language": "en-IN"}).status_code == 200

            manual_tx = client.post(
                "/api/transactions",
                json={"customer_id": kiran_id, "amount": 250, "tx_type": "credit", "note": "Manual entry"},
            )
            assert manual_tx.status_code == 201
            assert len(client.get(f"/api/customers/{kiran_id}/transactions").json()) == 1
            all_txs = client.get("/api/transactions").json()
            assert len(all_txs) > 1
            assert any(tx["customer_id"] == kiran_id and tx["customer_name"] == "Kiran Shetty" for tx in all_txs)

            suresh = next(c for c in customers if c["name"] == "Suresh Patel")
            assert client.post("/api/reminders/send", json={"customer_id": suresh["id"], "language": "hi-IN"}).status_code == 403
            assert client.post("/api/reminders/send", json={"customer_id": kiran_id, "language": "en-IN"}).status_code == 200

            dash = client.get("/api/dashboard").json()
            assert dash["total_customers"] == 7
            assert len(client.get("/api/audit").json()) >= 10
            assert client.patch("/api/settings", json={"ephemeral_transcripts": True}).json()["ephemeral_transcripts"] == "true"
            assert "Kiran Shetty" in client.get("/api/export/csv").text
            assert client.get("/api/voice/history").status_code == 200
            wipe = client.delete("/api/privacy/transcripts").json()
            assert wipe["status"] == "wiped"
            assert wipe["deleted_voice_logs"] >= 4
            assert client.get("/api/voice/history").json() == []
    finally:
        set_container(None)


def test_mongo_ledger_query_answers():
    fake_client = _FakeMongoClient()
    store = MongoKhataStore(mongo_url="mongodb://localhost:27017", db_name="khata_q", client=fake_client)
    cipher = FieldCipher("query-secret")
    seed_synthetic_data(store, cipher)
    engine = LedgerEngine(store, cipher)

    known = engine.list_customer_names()
    plan = try_fast_path("Suresh ka total batao", "hi-IN", None, known)
    res = engine.process_turn("q1", plan, "hi-IN", "fast-path", LatencyBreakdown())
    assert res.status == "answered"
    assert "650" in res.spoken_reply

    q2 = try_fast_path("Aaj kitna udhaar gaya?", "hi-IN", None, known)
    res2 = engine.process_turn("q1", q2, "hi-IN", "fast-path", LatencyBreakdown())
    assert res2.status == "answered"
    assert "150" in res2.spoken_reply

    top = store.get_top_debtor()
    assert top is not None
    assert top[0] == "Suresh Patel"
    assert store.get_total_outstanding() > 0
    assert any(b.customer_name == "Mahesh Verma" for b in res2.customer_breakdown)
    dash = store.get_dashboard_summary(res2.transactions[0].transaction_date)
    assert any(item["customer_name"] == "Mahesh Verma" for item in dash["today_credit_customers"])
    assert len(dash["outstanding_customers"]) >= 1

    # Update & manual-only delete work identically on MongoKhataStore
    custs = store.list_customers_with_totals()
    target_id = int(custs[0]["id"])
    updated = store.update_customer(
        target_id,
        name="Suresh Updated",
        phone_enc=cipher.encrypt("9123456789"),
        reminder_opt_in=True,
        preferred_language="kn-IN",
    )
    assert updated is not None
    assert updated["name"] == "Suresh Updated"
    assert store.delete_customer(target_id) is True
    assert store.get_customer_by_id(target_id) is None
    assert store.list_customer_transactions(target_id) == []
    assert store.delete_customer(999999) is False


def test_build_container_falls_back_to_sqlite_when_mongo_fails(tmp_path, monkeypatch):
    def _boom_mongo(url, **kwargs):
        raise RuntimeError(f"SSL handshake failed: [SSL: TLSV1_ALERT_INTERNAL_ERROR] connecting {url}")

    monkeypatch.setattr("app.db.store.MongoClient", _boom_mongo)

    settings = Settings(
        gemini_api_key="",
        gnani_api_key="",
        ledger_encryption_key="fallback-secret",
        database_path=str(tmp_path / "fallback.db"),
        mongodb_url="mongodb+srv://dbuser:supersecretpass@cluster0.mongodb.net/kirana_prod",
        mongodb_db_name="voice_khata",
    )
    container = build_container(settings=settings)
    try:
        assert container.db.backend_name == "sqlite"
        assert container.storage_fallback_reason is not None
        assert "0.0.0.0/0" in container.storage_fallback_reason
        assert "supersecretpass" not in container.storage_fallback_reason
        assert container.db.count_customers() == 6
    finally:
        if hasattr(container.db, "close"):
            container.db.close()
