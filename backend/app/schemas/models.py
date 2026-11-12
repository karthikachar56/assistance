"""Strict Pydantic schemas used for FastAPI endpoints and Gemini structured outputs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


TxType = Literal["credit", "payment"]
LanguageCode = Literal["hi-IN", "kn-IN", "ta-IN", "te-IN", "en-IN"]
NavTab = Literal["home", "customers", "settings"]
IntentType = Literal[
    "record_transactions",
    "correction",
    "disambiguation_reply",
    "confirmation_reply",
    "query_ledger",
    "navigate_page",
    "create_customer",
    "update_customer",
    "delete_customer_blocked",
    "greeting",
    "unknown",
]
QueryKind = Literal[
    "customer_balance",
    "customer_history",
    "credit_sum_for_date",
    "payment_sum_for_date",
    "top_debtor",
    "total_outstanding",
    "all_customers",
]


class ExtractedOperation(BaseModel):
    """Single financial operation extracted from speech or text."""

    customer_name: str = Field(description="Spoken customer name, e.g. 'Ramesh' or 'Ramesh Gowda'")
    amount: float = Field(gt=0, description="Exact numeric amount in INR")
    tx_type: TxType = Field(description="'credit' (udhaar/baaki) or 'payment' (diya/jama/paid)")
    date_phrase: str = Field(
        default="today",
        description="Normalized date hint: 'today', 'yesterday', 'day_before_yesterday', or ISO YYYY-MM-DD",
    )
    ambiguous_amounts: list[float] = Field(
        default_factory=list,
        description="Alternative amounts if audio/noise made numbers confusable (e.g. [340, 430])",
    )
    direction_ambiguous: bool = Field(
        default=False,
        description="True when phrasing like 'ko dena hai' makes credit vs payment direction unclear",
    )
    note: str | None = Field(default=None, description="Optional short item/payment note")


class ExtractedQuery(BaseModel):
    """Structured ledger query extracted from a spoken question."""

    kind: QueryKind
    customer_name: str | None = None
    date_phrase: str = "today"


class VoiceTurnPlan(BaseModel):
    """Strict structured output returned by Gemini AI or Fast-Path."""

    transcript: str = Field(description="Clean verbatim code-mixed transcript of the utterance")
    intent: IntentType
    operations: list[ExtractedOperation] = Field(default_factory=list)
    query: ExtractedQuery | None = None
    corrected_amount: float | None = Field(
        default=None,
        description="New amount when intent is 'correction' (e.g. 'nahi, 430')",
    )
    corrected_customer: str | None = Field(
        default=None,
        description="New customer name when intent is 'correction'",
    )
    corrected_tx_type: TxType | None = Field(
        default=None,
        description="New transaction type when intent corrects credit vs payment",
    )
    selected_customer: str | None = Field(
        default=None,
        description="Customer name chosen when answering a disambiguation prompt",
    )
    confirmed: bool | None = Field(
        default=None,
        description="True/False when replying to a pending confirmation prompt",
    )
    target_page: NavTab | None = Field(
        default=None,
        description="UI page to open when intent is 'navigate_page' ('home', 'customers', 'settings')",
    )
    new_customer_name: str | None = Field(
        default=None,
        description="Full customer name when intent is 'create_customer'",
    )
    new_customer_phone: str | None = Field(
        default=None,
        description="Optional phone number when intent is 'create_customer'",
    )
    new_customer_opt_in: bool = Field(
        default=False,
        description="Whether customer opted in to reminders when intent is 'create_customer'",
    )
    edit_target_customer: str | None = Field(
        default=None,
        description="Existing customer name to update when intent is 'update_customer'",
    )
    edit_new_name: str | None = Field(
        default=None,
        description="New full name when renaming an existing customer via 'update_customer'",
    )
    edit_new_phone: str | None = Field(
        default=None,
        description="Updated phone number when intent is 'update_customer'",
    )
    edit_opt_in: bool | None = Field(
        default=None,
        description="Updated reminder opt-in flag when intent is 'update_customer'",
    )
    edit_preferred_language: LanguageCode | None = Field(
        default=None,
        description="Updated preferred language code when intent is 'update_customer'",
    )
    confidence: float = Field(default=0.95, ge=0.0, le=1.0)
    needs_clarification: bool = False
    clarification_question: str | None = None
    detected_language: str = "hi-IN"


class VoiceTurnTextRequest(BaseModel):
    """Text-mode or browser-STT voice turn request."""

    session_id: str = Field(min_length=1, max_length=64)
    transcript: str = Field(min_length=1, max_length=1000)
    language: LanguageCode = "hi-IN"
    ai_mode: Literal["cascaded", "flash", "argon", "carbon", "offline"] | None = None
    include_tts: bool = False
    interactive_confirm: bool = False
    pending_action: dict | None = None
    last_committed_tx_ids: list[int] | None = None


class LatencyBreakdown(BaseModel):
    """Stage-by-stage timing telemetry in milliseconds for sub-2s tracking."""

    stt_ms: int = 0
    fast_path_ms: int = 0
    ai_ms: int = 0
    ledger_ms: int = 0
    tts_ms: int = 0
    total_ms: int = 0


class CustomerBreakdownItem(BaseModel):
    customer_id: int
    customer_name: str
    amount: float
    tx_count: int = 1


class TransactionOut(BaseModel):
    id: int
    customer_id: int
    customer_name: str
    amount: float
    tx_type: TxType
    transaction_date: str
    status: str
    note: str | None = None
    confidence: float
    created_at: str


class VoiceTurnResponse(BaseModel):
    """Complete response returned after processing one voice turn."""

    session_id: str
    transcript: str
    intent: IntentType
    status: Literal[
        "recorded",
        "corrected",
        "answered",
        "needs_disambiguation",
        "needs_confirmation",
        "cancelled",
        "error",
    ]
    spoken_reply: str
    ai_disclosure_prepended: bool = False
    model_used: str
    transactions: list[TransactionOut] = Field(default_factory=list)
    customer_breakdown: list[CustomerBreakdownItem] = Field(default_factory=list)
    candidate_customers: list[str] = Field(default_factory=list)
    pending_action: dict | None = None
    audio_base64: str | None = None
    audio_mime_type: str | None = None
    voice_log_id: int | None = None
    navigate_tab: NavTab | None = None
    highlight_customer_id: int | None = None
    highlight_customer_name: str | None = None
    open_customer_details: bool = False
    latency: LatencyBreakdown


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    phone: str | None = Field(default=None, max_length=24)
    reminder_opt_in: bool = False
    preferred_language: LanguageCode = "hi-IN"


class CustomerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    phone: str | None = Field(default=None, max_length=24)
    reminder_opt_in: bool | None = None
    preferred_language: LanguageCode | None = None


class CustomerOut(BaseModel):
    id: int
    name: str
    phone: str | None = None
    reminder_opt_in: bool
    preferred_language: str
    total_credit: float
    total_payment: float
    outstanding_balance: float
    is_synthetic: bool
    created_at: str


class ManualTransactionCreate(BaseModel):
    customer_id: int
    amount: float = Field(gt=0)
    tx_type: TxType
    transaction_date: str | None = None
    note: str | None = Field(default=None, max_length=200)


class DashboardSummary(BaseModel):
    today_credit: float
    today_payment: float
    total_outstanding: float
    total_customers: int
    recent_transactions: list[TransactionOut]
    today_credit_customers: list[CustomerBreakdownItem] = Field(default_factory=list)
    today_payment_customers: list[CustomerBreakdownItem] = Field(default_factory=list)
    outstanding_customers: list[CustomerBreakdownItem] = Field(default_factory=list)


class ReminderRequest(BaseModel):
    customer_id: int
    language: LanguageCode = "hi-IN"


class ReminderResponse(BaseModel):
    customer_id: int
    customer_name: str
    outstanding_balance: float
    message: str
    opted_in: bool
    sent: bool


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=600)
    language: LanguageCode = "hi-IN"


class LiveTokenRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    language: LanguageCode = "hi-IN"
    extended: bool = False
    interactive_confirm: bool = False
    voice: str | None = None
    resume_handle: str | None = None


class LiveToolExecuteRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    language: LanguageCode = "hi-IN"
    model: str = "gemini-3.8-live"
    tool_name: str = Field(min_length=1, max_length=80)
    args: dict[str, Any] = Field(default_factory=dict)
    interactive_confirm: bool = False
    pending_action: dict | None = None
    last_committed_tx_ids: list[int] | None = None


class SettingsUpdate(BaseModel):
    preferred_language: LanguageCode | None = None
    ai_mode: Literal["cascaded", "flash", "argon", "carbon", "offline"] | None = None
    speech_provider: Literal["auto", "gemini", "gnani", "browser"] | None = None
    ephemeral_transcripts: bool | None = None
    dpdp_consent_accepted: bool | None = None

