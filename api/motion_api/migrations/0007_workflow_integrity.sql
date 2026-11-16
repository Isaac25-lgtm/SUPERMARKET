-- Reserve money operations before any business writes. The reservation and its
-- result are committed in the same transaction.
ALTER TABLE motion.operation
    ALTER COLUMN result_type DROP NOT NULL,
    ALTER COLUMN result_id DROP NOT NULL,
    ADD CONSTRAINT operation_result_pair CHECK ((result_type IS NULL) = (result_id IS NULL));

-- Separate application guards from the Tryton business tables.
CREATE TABLE motion.supplier_invoice_ref (
    company_id int NOT NULL,
    supplier_id int NOT NULL,
    reference text NOT NULL,
    invoice_id int NOT NULL,
    PRIMARY KEY (company_id, supplier_id, reference)
);

CREATE TABLE motion.purchase_billing_guard (
    purchase_id int PRIMARY KEY,
    version bigint NOT NULL DEFAULT 1
);

CREATE TABLE motion.safe_guard (
    branch_id int PRIMARY KEY,
    version bigint NOT NULL DEFAULT 1
);
