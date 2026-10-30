from __future__ import annotations

import unittest
from phase4.contract import AuditEvidence, AuditInput, InvoiceInfo, ReconciliationStatus


class TestPhase4Contract(unittest.TestCase):
    def test_audit_contract_serialization(self):
        inv = InvoiceInfo(
            invoice_number="INV-2026-001",
            invoice_date="2026-09-15",
            subtotal=10000.0,
            invoice_total=11800.0,
            cgst_total=900.0,
            sgst_total=900.0,
        )
        evidence = AuditEvidence(invoice=inv)
        audit_in = AuditInput(
            query_id="TEST_01",
            query="Why is there an ₹1,800 difference?",
            evidence=evidence,
        )

        d = audit_in.model_dump()
        self.assertEqual(d["query_id"], "TEST_01")
        self.assertEqual(d["evidence"]["invoice"]["invoice_total"], 11800.0)
        self.assertEqual(ReconciliationStatus.DISCREPANCY.value, "DISCREPANCY")


if __name__ == "__main__":
    unittest.main()
