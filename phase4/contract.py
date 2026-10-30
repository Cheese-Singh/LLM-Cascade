from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ReconciliationStatus(str, Enum):
    MATCHED = "MATCHED"
    DISCREPANCY = "DISCREPANCY"
    UNSUPPORTED = "UNSUPPORTED"
    CONTRADICTORY = "CONTRADICTORY"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class BusinessInfo(BaseModel):
    business_id: Optional[str] = None
    business_name: str
    gstin: Optional[str] = None
    pan: Optional[str] = None
    state_code: Optional[str] = None
    address: Optional[Dict[str, Any]] = None
    default_currency: str = "INR"


class InvoiceItemInfo(BaseModel):
    line_number: int
    description: str
    hsn_sac_code: Optional[str] = None
    quantity: Optional[float] = 1.0
    unit: Optional[str] = "NOS"
    unit_price: Optional[float] = 0.0
    discount_amount: float = 0.0
    taxable_value: float
    gst_rate: float
    cgst_amount: float = 0.0
    sgst_amount: float = 0.0
    igst_amount: float = 0.0
    cess_amount: float = 0.0
    line_total: float


class InvoicePaymentInfo(BaseModel):
    payment_id: Optional[str] = None
    payment_date: str
    amount: float
    payment_mode: Optional[str] = None
    reference_number: Optional[str] = None


class InvoiceInfo(BaseModel):
    invoice_id: Optional[str] = None
    invoice_number: str
    invoice_date: str
    due_date: Optional[str] = None
    invoice_type: str = "tax_invoice"
    seller_name: Optional[str] = None
    seller_gstin: Optional[str] = None
    seller_pan: Optional[str] = None
    seller_address: Optional[Dict[str, Any]] = None
    buyer_name: Optional[str] = None
    buyer_gstin: Optional[str] = None
    buyer_pan: Optional[str] = None
    buyer_address: Optional[Dict[str, Any]] = None
    place_of_supply: Optional[str] = None
    reverse_charge: bool = False
    subtotal: float
    discount_amount: float = 0.0
    cgst_total: float = 0.0
    sgst_total: float = 0.0
    igst_total: float = 0.0
    cess_total: float = 0.0
    round_off: float = 0.0
    invoice_total: float
    currency: str = "INR"
    payment_terms: Optional[str] = None
    notes: Optional[str] = None


class ProvenanceInfo(BaseModel):
    provenance_id: str
    field_name: str
    field_value: Optional[str] = None
    page_number: Optional[int] = 1
    source_text: Optional[str] = None
    confidence: Optional[float] = 100.0


class DocumentChunkInfo(BaseModel):
    chunk_id: Optional[int] = None
    file_name: Optional[str] = None
    chunk_index: int = 0
    content: str


class AuditEvidence(BaseModel):
    business: Optional[BusinessInfo] = None
    invoice: Optional[InvoiceInfo] = None
    items: List[InvoiceItemInfo] = Field(default_factory=list)
    payments: List[InvoicePaymentInfo] = Field(default_factory=list)
    provenance: List[ProvenanceInfo] = Field(default_factory=list)
    document_chunks: List[DocumentChunkInfo] = Field(default_factory=list)
    raw_text: Optional[str] = None


class AuditInput(BaseModel):
    query_id: str
    query: str
    evidence: AuditEvidence


class AuditAnswer(BaseModel):
    query_id: str
    answer: str
    status: ReconciliationStatus
    discrepancy_amount: Optional[float] = None
    discrepancy_reason: Optional[str] = None
    supporting_citations: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    unresolved_uncertainty: Optional[str] = None
    tier: Optional[str] = None
    latency_ms: Optional[float] = None
    total_tokens: Optional[int] = None
