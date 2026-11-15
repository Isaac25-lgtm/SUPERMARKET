-- Retail & foodstuff attributes that Tryton's base product model does not carry.
-- Product ids reference Tryton product_product.id.

CREATE TABLE motion.product_policy (
    product_id          int PRIMARY KEY,
    sold_by_weight      boolean NOT NULL DEFAULT false,   -- priced per kg; fractional quantities at the till
    plu                 text UNIQUE,                       -- scale / manual price-lookup code (5 digits)
    perishable          boolean NOT NULL DEFAULT false,
    shelf_life_days     int CHECK (shelf_life_days IS NULL OR shelf_life_days > 0),
    reorder_point       numeric(18, 3) NOT NULL DEFAULT 0,
    reorder_qty         numeric(18, 3) NOT NULL DEFAULT 0,
    preferred_supplier  int,                               -- Tryton party.party id
    lead_time_days      int NOT NULL DEFAULT 3,
    updated_at          timestamptz NOT NULL DEFAULT now()
);

-- Branch-level reorder overrides (a fast seller at Central may need a higher point).
CREATE TABLE motion.product_branch_policy (
    product_id    int NOT NULL,
    branch_id     int NOT NULL,
    reorder_point numeric(18, 3) NOT NULL,
    reorder_qty   numeric(18, 3) NOT NULL,
    PRIMARY KEY (product_id, branch_id)
);

-- Batches / lots received with expiry dates (perishable foodstuff).
-- Remaining quantity per batch is ESTIMATED by allocating current on-hand
-- stock to the most recently received batches first (stock rotates FEFO/FIFO);
-- till sales do not record which batch was sold.
CREATE TABLE motion.stock_batch (
    id            bigserial PRIMARY KEY,
    product_id    int NOT NULL,
    branch_id     int NOT NULL,
    lot_ref       text,
    expiry_date   date,
    received_qty  numeric(18, 3) NOT NULL CHECK (received_qty > 0),
    received_at   timestamptz NOT NULL DEFAULT now(),
    source_type   text NOT NULL,         -- purchase_receipt | transfer_receipt | opening
    source_id     text,
    created_by    bigint REFERENCES motion.app_user(id)
);
CREATE INDEX stock_batch_lookup_idx ON motion.stock_batch (branch_id, product_id, received_at DESC);
CREATE INDEX stock_batch_expiry_idx ON motion.stock_batch (expiry_date) WHERE expiry_date IS NOT NULL;

-- Price history (who changed which price when) — complements the audit trail
-- and lets offline edges detect price-version divergence.
CREATE TABLE motion.price_change (
    id          bigserial PRIMARY KEY,
    product_id  int NOT NULL,
    old_price   numeric(18, 2),
    new_price   numeric(18, 2) NOT NULL CHECK (new_price >= 0),
    reason      text NOT NULL,
    changed_by  bigint NOT NULL REFERENCES motion.app_user(id),
    changed_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX price_change_product_idx ON motion.price_change (product_id, changed_at DESC);
