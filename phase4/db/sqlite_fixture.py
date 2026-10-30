from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from phase4.config import SQLITE_DB_PATH


def get_sqlite_connection(db_path: Path = SQLITE_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_sqlite_db(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.executescript("""
    CREATE TABLE IF NOT EXISTS business (
        business_id TEXT PRIMARY KEY,
        business_name TEXT NOT NULL,
        gstin TEXT,
        pan TEXT,
        state_code TEXT,
        address TEXT,
        default_currency TEXT NOT NULL DEFAULT 'INR',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS source_file (
        file_id TEXT PRIMARY KEY,
        business_id TEXT NOT NULL REFERENCES business(business_id) ON DELETE CASCADE,
        file_name TEXT NOT NULL,
        mime_type TEXT,
        storage_path TEXT NOT NULL,
        file_hash TEXT,
        page_count INTEGER,
        processing_status TEXT NOT NULL DEFAULT 'pending',
        uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS invoice (
        invoice_id TEXT PRIMARY KEY,
        business_id TEXT REFERENCES business(business_id) ON DELETE SET NULL,
        file_id TEXT REFERENCES source_file(file_id) ON DELETE SET NULL,
        invoice_number TEXT NOT NULL,
        invoice_date TEXT NOT NULL,
        due_date TEXT,
        invoice_type TEXT DEFAULT 'tax_invoice',
        seller_name TEXT,
        seller_gstin TEXT,
        seller_pan TEXT,
        seller_address TEXT,
        buyer_name TEXT,
        buyer_gstin TEXT,
        buyer_pan TEXT,
        buyer_address TEXT,
        place_of_supply TEXT,
        reverse_charge INTEGER DEFAULT 0,
        subtotal REAL NOT NULL,
        discount_amount REAL DEFAULT 0.0,
        cgst_total REAL DEFAULT 0.0,
        sgst_total REAL DEFAULT 0.0,
        igst_total REAL DEFAULT 0.0,
        cess_total REAL DEFAULT 0.0,
        round_off REAL DEFAULT 0.0,
        invoice_total REAL NOT NULL,
        currency TEXT NOT NULL DEFAULT 'INR',
        payment_terms TEXT,
        notes TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS invoice_item (
        item_id TEXT PRIMARY KEY,
        invoice_id TEXT NOT NULL REFERENCES invoice(invoice_id) ON DELETE CASCADE,
        line_number INTEGER NOT NULL,
        description TEXT NOT NULL,
        hsn_sac_code TEXT,
        quantity REAL DEFAULT 1.0,
        unit TEXT DEFAULT 'NOS',
        unit_price REAL DEFAULT 0.0,
        discount_amount REAL DEFAULT 0.0,
        taxable_value REAL NOT NULL,
        gst_rate REAL NOT NULL,
        cgst_amount REAL DEFAULT 0.0,
        sgst_amount REAL DEFAULT 0.0,
        igst_amount REAL DEFAULT 0.0,
        cess_amount REAL DEFAULT 0.0,
        line_total REAL NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS invoice_payment (
        payment_id TEXT PRIMARY KEY,
        invoice_id TEXT NOT NULL REFERENCES invoice(invoice_id) ON DELETE CASCADE,
        payment_date TEXT NOT NULL,
        amount REAL NOT NULL,
        payment_mode TEXT,
        reference_number TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS invoice_extraction (
        extraction_id TEXT PRIMARY KEY,
        invoice_id TEXT NOT NULL REFERENCES invoice(invoice_id) ON DELETE CASCADE,
        extraction_status TEXT NOT NULL,
        extraction_method TEXT,
        overall_confidence REAL,
        raw_text TEXT,
        error_message TEXT,
        processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS invoice_field_provenance (
        provenance_id TEXT PRIMARY KEY,
        invoice_id TEXT NOT NULL REFERENCES invoice(invoice_id) ON DELETE CASCADE,
        extraction_id TEXT REFERENCES invoice_extraction(extraction_id) ON DELETE SET NULL,
        field_name TEXT NOT NULL,
        field_value TEXT,
        page_number INTEGER,
        source_text TEXT,
        location TEXT,
        confidence REAL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS document_chunks (
        chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
        business_id TEXT NOT NULL,
        source_file_id TEXT NOT NULL,
        file_name TEXT,
        chunk_index INTEGER NOT NULL,
        content TEXT NOT NULL,
        embedding TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """)
    conn.commit()


def insert_business(conn: sqlite3.Connection, b: Dict[str, Any]) -> str:
    b_id = b.get("business_id") or str(uuid.uuid4())
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO business (business_id, business_name, gstin, pan, state_code, address, default_currency)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(business_id) DO UPDATE SET business_name=excluded.business_name;
    """, (
        b_id,
        b["business_name"],
        b.get("gstin"),
        b.get("pan"),
        b.get("state_code"),
        json.dumps(b.get("address")) if isinstance(b.get("address"), dict) else b.get("address"),
        b.get("default_currency", "INR"),
    ))
    conn.commit()
    return b_id


def insert_invoice(conn: sqlite3.Connection, inv: Dict[str, Any], business_id: str, file_id: Optional[str] = None) -> str:
    inv_id = inv.get("invoice_id") or str(uuid.uuid4())
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO invoice (
        invoice_id, business_id, file_id, invoice_number, invoice_date, due_date,
        invoice_type, seller_name, seller_gstin, seller_pan, seller_address,
        buyer_name, buyer_gstin, buyer_pan, buyer_address, place_of_supply,
        reverse_charge, subtotal, discount_amount, cgst_total, sgst_total,
        igst_total, cess_total, round_off, invoice_total, currency, payment_terms, notes
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(invoice_id) DO UPDATE SET invoice_number=excluded.invoice_number;
    """, (
        inv_id, business_id, file_id,
        inv["invoice_number"], inv["invoice_date"], inv.get("due_date"),
        inv.get("invoice_type", "tax_invoice"),
        inv.get("seller_name"), inv.get("seller_gstin"), inv.get("seller_pan"),
        json.dumps(inv.get("seller_address")) if isinstance(inv.get("seller_address"), dict) else inv.get("seller_address"),
        inv.get("buyer_name"), inv.get("buyer_gstin"), inv.get("buyer_pan"),
        json.dumps(inv.get("buyer_address")) if isinstance(inv.get("buyer_address"), dict) else inv.get("buyer_address"),
        inv.get("place_of_supply"),
        1 if inv.get("reverse_charge") else 0,
        float(inv["subtotal"]),
        float(inv.get("discount_amount", 0.0)),
        float(inv.get("cgst_total", 0.0)),
        float(inv.get("sgst_total", 0.0)),
        float(inv.get("igst_total", 0.0)),
        float(inv.get("cess_total", 0.0)),
        float(inv.get("round_off", 0.0)),
        float(inv["invoice_total"]),
        inv.get("currency", "INR"),
        inv.get("payment_terms"),
        inv.get("notes")
    ))
    conn.commit()
    return inv_id
