from __future__ import annotations

import sqlite3
import unittest
from phase4.db.sqlite_fixture import get_sqlite_connection, init_sqlite_db, insert_business, insert_invoice


class TestPhase4Database(unittest.TestCase):
    def test_sqlite_fixture_crud(self):
        # In-memory database for testing
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        init_sqlite_db(conn)

        # Insert business
        biz = {
            "business_id": "test_biz_1",
            "business_name": "Test Technologies Ltd",
            "gstin": "27TESTB1234T1Z1",
            "pan": "TESTB1234T",
            "state_code": "27",
            "address": {"city": "Mumbai"},
        }
        b_id = insert_business(conn, biz)
        self.assertEqual(b_id, "test_biz_1")

        # Insert invoice
        inv = {
            "invoice_id": "test_inv_1",
            "invoice_number": "INV-TEST-001",
            "invoice_date": "2026-09-01",
            "subtotal": 10000.0,
            "invoice_total": 11800.0,
            "cgst_total": 900.0,
            "sgst_total": 900.0,
        }
        inv_id = insert_invoice(conn, inv, business_id=b_id)
        self.assertEqual(inv_id, "test_inv_1")

        # Verify query
        cur = conn.cursor()
        cur.execute("SELECT * FROM invoice WHERE invoice_id = ?", (inv_id,))
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["invoice_number"], "INV-TEST-001")
        self.assertEqual(row["invoice_total"], 11800.0)


if __name__ == "__main__":
    unittest.main()
