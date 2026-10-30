-- Authoritative PostgreSQL Schema for LLM-Cascade Phase IV
-- Supports PostgreSQL 16 with pgvector extension

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Safe attempt to enable pgvector extension if available
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS vector;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'pgvector extension not installed; falling back to double precision arrays for embeddings.';
END $$;

-- 1. Business
CREATE TABLE IF NOT EXISTS public.business (
    business_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_name VARCHAR(255) NOT NULL,
    gstin VARCHAR(15),
    pan VARCHAR(10),
    state_code VARCHAR(2),
    address JSONB,
    default_currency VARCHAR(3) NOT NULL DEFAULT 'INR',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. Source File
CREATE TABLE IF NOT EXISTS public.source_file (
    file_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES public.business(business_id) ON DELETE CASCADE,
    file_name VARCHAR(255) NOT NULL,
    mime_type VARCHAR(100),
    storage_path TEXT NOT NULL,
    file_hash VARCHAR(64),
    page_count INT CHECK (page_count > 0),
    processing_status VARCHAR(50) NOT NULL DEFAULT 'pending',
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3. Invoice
CREATE TABLE IF NOT EXISTS public.invoice (
    invoice_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID REFERENCES public.business(business_id) ON DELETE SET NULL,
    file_id UUID REFERENCES public.source_file(file_id) ON DELETE SET NULL,
    invoice_number VARCHAR(100) NOT NULL,
    invoice_date DATE NOT NULL,
    due_date DATE,
    invoice_type VARCHAR(50) DEFAULT 'tax_invoice',
    seller_name VARCHAR(255),
    seller_gstin VARCHAR(15),
    seller_pan VARCHAR(10),
    seller_address JSONB,
    buyer_name VARCHAR(255),
    buyer_gstin VARCHAR(15),
    buyer_pan VARCHAR(10),
    buyer_address JSONB,
    place_of_supply VARCHAR(2),
    reverse_charge BOOLEAN DEFAULT FALSE,
    subtotal NUMERIC(15, 2) NOT NULL,
    discount_amount NUMERIC(15, 2) DEFAULT 0.00,
    cgst_total NUMERIC(15, 2) DEFAULT 0.00,
    sgst_total NUMERIC(15, 2) DEFAULT 0.00,
    igst_total NUMERIC(15, 2) DEFAULT 0.00,
    cess_total NUMERIC(15, 2) DEFAULT 0.00,
    round_off NUMERIC(15, 2) DEFAULT 0.00,
    invoice_total NUMERIC(15, 2) NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'INR',
    payment_terms TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 4. Invoice Item
CREATE TABLE IF NOT EXISTS public.invoice_item (
    item_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id UUID NOT NULL REFERENCES public.invoice(invoice_id) ON DELETE CASCADE,
    line_number INT NOT NULL,
    description TEXT NOT NULL,
    hsn_sac_code VARCHAR(10),
    quantity NUMERIC(12, 3) DEFAULT 1.0,
    unit VARCHAR(20) DEFAULT 'NOS',
    unit_price NUMERIC(15, 2) DEFAULT 0.00,
    discount_amount NUMERIC(15, 2) DEFAULT 0.00,
    taxable_value NUMERIC(15, 2) NOT NULL,
    gst_rate NUMERIC(5, 2) NOT NULL,
    cgst_amount NUMERIC(15, 2) DEFAULT 0.00,
    sgst_amount NUMERIC(15, 2) DEFAULT 0.00,
    igst_amount NUMERIC(15, 2) DEFAULT 0.00,
    cess_amount NUMERIC(15, 2) DEFAULT 0.00,
    line_total NUMERIC(15, 2) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 5. Invoice Payment
CREATE TABLE IF NOT EXISTS public.invoice_payment (
    payment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id UUID NOT NULL REFERENCES public.invoice(invoice_id) ON DELETE CASCADE,
    payment_date DATE NOT NULL,
    amount NUMERIC(15, 2) NOT NULL,
    payment_mode VARCHAR(50),
    reference_number VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 6. Invoice Extraction
CREATE TABLE IF NOT EXISTS public.invoice_extraction (
    extraction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id UUID NOT NULL REFERENCES public.invoice(invoice_id) ON DELETE CASCADE,
    extraction_status VARCHAR(50) NOT NULL,
    extraction_method VARCHAR(50),
    overall_confidence NUMERIC(5, 2) CHECK (overall_confidence BETWEEN 0 AND 100),
    raw_text TEXT,
    error_message TEXT,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 7. Invoice Field Provenance
CREATE TABLE IF NOT EXISTS public.invoice_field_provenance (
    provenance_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id UUID NOT NULL REFERENCES public.invoice(invoice_id) ON DELETE CASCADE,
    extraction_id UUID REFERENCES public.invoice_extraction(extraction_id) ON DELETE SET NULL,
    field_name VARCHAR(100) NOT NULL,
    field_value TEXT,
    page_number INT CHECK (page_number > 0),
    source_text TEXT,
    location JSONB,
    confidence NUMERIC(5, 2) CHECK (confidence BETWEEN 0 AND 100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 8. Document Chunks
-- Note: The supplied logical schema does not declare foreign keys on this table.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'vector') THEN
        CREATE TABLE IF NOT EXISTS public.document_chunks (
            chunk_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            business_id UUID NOT NULL,
            source_file_id UUID NOT NULL,
            file_name VARCHAR(255),
            chunk_index INT NOT NULL CHECK (chunk_index >= 0),
            content TEXT NOT NULL,
            embedding vector(1536) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    ELSE
        CREATE TABLE IF NOT EXISTS public.document_chunks (
            chunk_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            business_id UUID NOT NULL,
            source_file_id UUID NOT NULL,
            file_name VARCHAR(255),
            chunk_index INT NOT NULL CHECK (chunk_index >= 0),
            content TEXT NOT NULL,
            embedding DOUBLE PRECISION[] NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    END IF;
END $$;
