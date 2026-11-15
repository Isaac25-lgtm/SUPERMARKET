-- Motion platform schema. Lives beside Tryton's tables (schema public) in the
-- same database so platform records commit atomically with Tryton postings.
-- Money is NUMERIC (never float). Timestamps are timestamptz (stored UTC).
CREATE SCHEMA IF NOT EXISTS motion;

-- ---------------------------------------------------------------- identity
CREATE TABLE motion.role (
    code        text PRIMARY KEY,
    name        text NOT NULL,
    description text NOT NULL,
    rank        int  NOT NULL
);

CREATE TABLE motion.app_user (
    id                   bigserial PRIMARY KEY,
    username             text NOT NULL,
    full_name            text NOT NULL,
    email                text,
    password_hash        text NOT NULL,
    status               text NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active', 'suspended')),
    all_branches         boolean NOT NULL DEFAULT false,
    must_change_password boolean NOT NULL DEFAULT true,
    tryton_user_id       int,
    tryton_employee_id   int,
    failed_logins        int NOT NULL DEFAULT 0,
    locked_until         timestamptz,
    last_login_at        timestamptz,
    password_changed_at  timestamptz,
    pos_pin_hash         text,
    created_at           timestamptz NOT NULL DEFAULT now(),
    created_by           bigint REFERENCES motion.app_user(id),
    updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX app_user_username_uq ON motion.app_user (lower(username));

CREATE TABLE motion.user_role (
    user_id    bigint NOT NULL REFERENCES motion.app_user(id),
    role_code  text   NOT NULL REFERENCES motion.role(code),
    granted_by bigint REFERENCES motion.app_user(id),
    granted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, role_code)
);

-- branch_id references Tryton's motion_branch table (motion.branch model).
CREATE TABLE motion.user_branch (
    user_id    bigint NOT NULL REFERENCES motion.app_user(id),
    branch_id  int    NOT NULL,
    granted_by bigint REFERENCES motion.app_user(id),
    granted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, branch_id)
);

CREATE TABLE motion.session (
    id_hash        text PRIMARY KEY,           -- sha256(token); raw token never stored
    user_id        bigint NOT NULL REFERENCES motion.app_user(id),
    csrf_hash      text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    last_seen_at   timestamptz NOT NULL DEFAULT now(),
    expires_at     timestamptz NOT NULL,
    revoked_at     timestamptz,
    revoked_reason text,
    ip             text,
    user_agent     text
);
CREATE INDEX session_user_idx ON motion.session (user_id) WHERE revoked_at IS NULL;

CREATE TABLE motion.login_attempt (
    id       bigserial PRIMARY KEY,
    username text NOT NULL,
    ip       text,
    success  boolean NOT NULL,
    reason   text,
    at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX login_attempt_user_at_idx ON motion.login_attempt (lower(username), at DESC);
CREATE INDEX login_attempt_ip_at_idx ON motion.login_attempt (ip, at DESC);

-- ---------------------------------------------------------------- audit (append-only)
CREATE TABLE motion.audit_event (
    id             bigserial PRIMARY KEY,
    at             timestamptz NOT NULL DEFAULT clock_timestamp(),
    actor_id       bigint,
    actor_username text,
    actor_roles    text[] NOT NULL DEFAULT '{}',
    branch_id      int,
    action         text NOT NULL,
    outcome        text NOT NULL CHECK (outcome IN ('success', 'denied', 'failure')),
    target_type    text,
    target_id      text,
    amount         numeric(18, 2),
    reason         text,
    before_state   jsonb,
    after_state    jsonb,
    request_id     text,
    ip             text,
    user_agent     text,
    session_ref    text            -- first 12 chars of session hash, never the token
);
CREATE INDEX audit_at_idx ON motion.audit_event (at DESC);
CREATE INDEX audit_actor_idx ON motion.audit_event (actor_id, at DESC);
CREATE INDEX audit_target_idx ON motion.audit_event (target_type, target_id);
CREATE INDEX audit_action_idx ON motion.audit_event (action, at DESC);
CREATE INDEX audit_branch_idx ON motion.audit_event (branch_id, at DESC);

CREATE FUNCTION motion.audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'motion.audit_event is append-only (% rejected)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END $$;
CREATE TRIGGER audit_no_update BEFORE UPDATE OR DELETE ON motion.audit_event
    FOR EACH ROW EXECUTE FUNCTION motion.audit_immutable();
CREATE TRIGGER audit_no_truncate BEFORE TRUNCATE ON motion.audit_event
    FOR EACH STATEMENT EXECUTE FUNCTION motion.audit_immutable();

-- ---------------------------------------------------------------- approvals
CREATE TABLE motion.approval_policy (
    id          bigserial PRIMARY KEY,
    action_type text NOT NULL,
    branch_id   int,                       -- NULL = all branches
    min_amount  numeric(18, 2) NOT NULL DEFAULT 0,
    max_amount  numeric(18, 2),            -- NULL = no upper bound
    steps       jsonb NOT NULL,            -- [{"role": "...", "label": "...", "kind": "review|verify|approve"}]
    active      boolean NOT NULL DEFAULT true,
    note        text,
    updated_by  bigint REFERENCES motion.app_user(id),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CHECK (max_amount IS NULL OR max_amount >= min_amount)
);

CREATE SEQUENCE motion.approval_ref_seq;
CREATE TABLE motion.approval_request (
    id                 bigserial PRIMARY KEY,
    ref                text NOT NULL UNIQUE,
    action_type        text NOT NULL,
    branch_id          int NOT NULL,
    amount             numeric(18, 2) NOT NULL DEFAULT 0,
    currency           text NOT NULL DEFAULT 'UGX',
    title              text NOT NULL,
    justification      text,
    urgency            text NOT NULL DEFAULT 'normal' CHECK (urgency IN ('low', 'normal', 'high', 'critical')),
    payload            jsonb NOT NULL,
    status             text NOT NULL CHECK (status IN
                       ('pending', 'approved', 'rejected', 'returned', 'cancelled', 'executed')),
    current_step       int NOT NULL DEFAULT 0,
    steps              jsonb NOT NULL,     -- snapshot of the policy at submission
    policy_id          bigint REFERENCES motion.approval_policy(id),
    revision           int NOT NULL DEFAULT 1,
    fulfilment_status  text,
    result_type        text,
    result_id          text,
    initiator_id       bigint NOT NULL REFERENCES motion.app_user(id),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    due_at             timestamptz,
    consumed_at        timestamptz          -- single-use approvals (refunds, overrides)
);
CREATE INDEX approval_status_idx ON motion.approval_request (status, action_type);
CREATE INDEX approval_branch_idx ON motion.approval_request (branch_id, created_at DESC);

CREATE TABLE motion.approval_action (
    id          bigserial PRIMARY KEY,
    request_id  bigint NOT NULL REFERENCES motion.approval_request(id),
    step_index  int,
    actor_id    bigint NOT NULL REFERENCES motion.app_user(id),
    acting_role text,
    delegated_from bigint REFERENCES motion.app_user(id),
    action      text NOT NULL,
    comment     text,
    from_status text,
    to_status   text,
    payload_snapshot jsonb,
    at          timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX approval_action_req_idx ON motion.approval_action (request_id, at);
CREATE TRIGGER approval_action_no_update BEFORE UPDATE OR DELETE ON motion.approval_action
    FOR EACH ROW EXECUTE FUNCTION motion.audit_immutable();

CREATE TABLE motion.delegation (
    id         bigserial PRIMARY KEY,
    from_user  bigint NOT NULL REFERENCES motion.app_user(id),
    to_user    bigint NOT NULL REFERENCES motion.app_user(id),
    role_code  text NOT NULL REFERENCES motion.role(code),
    max_amount numeric(18, 2),
    starts_at  timestamptz NOT NULL,
    ends_at    timestamptz NOT NULL,
    reason     text NOT NULL,
    created_by bigint NOT NULL REFERENCES motion.app_user(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz,
    CHECK (from_user <> to_user),
    CHECK (ends_at > starts_at)
);

-- ---------------------------------------------------------------- POS
CREATE TABLE motion.shift (
    id                 bigserial PRIMARY KEY,
    branch_id          int NOT NULL,
    register_id        int NOT NULL,          -- Tryton sale.point
    cashier_id         bigint NOT NULL REFERENCES motion.app_user(id),
    tryton_session_id  int NOT NULL,          -- Tryton sale.point.cash.session
    status             text NOT NULL CHECK (status IN ('open', 'closed', 'reconciled', 'interrupted')),
    opened_at          timestamptz NOT NULL DEFAULT now(),
    opening_float      numeric(18, 2) NOT NULL,
    session_start_amount numeric(18, 2) NOT NULL,
    closed_at          timestamptz,
    counted_cash       numeric(18, 2),
    expected_cash      numeric(18, 2),
    variance           numeric(18, 2),
    variance_transfer_id int,
    close_note         text,
    closed_by          bigint REFERENCES motion.app_user(id),
    reconciled_by      bigint REFERENCES motion.app_user(id),
    reconciled_at      timestamptz,
    reconcile_note     text
);
CREATE UNIQUE INDEX shift_one_open_per_register ON motion.shift (register_id) WHERE status = 'open';
CREATE UNIQUE INDEX shift_one_open_per_cashier ON motion.shift (cashier_id) WHERE status = 'open';

CREATE TABLE motion.pos_sale (
    id               bigserial PRIMARY KEY,
    idempotency_key  text NOT NULL UNIQUE,
    tryton_sale_id   int NOT NULL UNIQUE,
    number           text NOT NULL,
    branch_id        int NOT NULL,
    register_id      int NOT NULL,
    shift_id         bigint REFERENCES motion.shift(id),
    cashier_id       bigint NOT NULL REFERENCES motion.app_user(id),
    source           text NOT NULL CHECK (source IN ('online', 'edge')),
    edge_device_id   bigint,
    local_created_at timestamptz,
    posted_at        timestamptz NOT NULL DEFAULT now(),
    sale_date        date NOT NULL,
    total            numeric(18, 2) NOT NULL,
    total_tax        numeric(18, 2) NOT NULL,
    discount_total   numeric(18, 2) NOT NULL DEFAULT 0,
    kind             text NOT NULL CHECK (kind IN ('sale', 'refund')),
    refund_of        bigint REFERENCES motion.pos_sale(id),
    approval_id      bigint REFERENCES motion.approval_request(id),
    payments         jsonb NOT NULL,
    unverified_claims boolean NOT NULL DEFAULT false
);
CREATE UNIQUE INDEX pos_sale_number_uq ON motion.pos_sale (register_id, number);
CREATE INDEX pos_sale_branch_date_idx ON motion.pos_sale (branch_id, sale_date);
CREATE INDEX pos_sale_cashier_idx ON motion.pos_sale (cashier_id, posted_at DESC);

CREATE TABLE motion.pos_sale_line (
    id              bigserial PRIMARY KEY,
    pos_sale_id     bigint NOT NULL REFERENCES motion.pos_sale(id),
    tryton_line_id  int NOT NULL UNIQUE,
    product_id      int NOT NULL,
    quantity        numeric(18, 3) NOT NULL,
    unit_price      numeric(18, 2) NOT NULL,     -- tax-inclusive price actually charged
    list_unit_price numeric(18, 2) NOT NULL,     -- catalog gross price at time of sale
    discount_pct    numeric(6, 2) NOT NULL DEFAULT 0,
    refunded_qty    numeric(18, 3) NOT NULL DEFAULT 0,
    refund_of_line  bigint REFERENCES motion.pos_sale_line(id),
    CHECK (refunded_qty >= 0),
    CHECK (quantity < 0 OR refunded_qty <= quantity)
);

-- Serialises concurrent sales of the same product at the same location so a
-- REPEATABLE READ snapshot cannot oversell the last unit.
CREATE TABLE motion.stock_guard (
    location_id int NOT NULL,
    product_id  int NOT NULL,
    version     bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (location_id, product_id)
);

-- ---------------------------------------------------------------- transfers & discrepancies
CREATE SEQUENCE motion.transfer_ref_seq;
CREATE TABLE motion.transfer (
    id                 bigserial PRIMARY KEY,
    ref                text NOT NULL UNIQUE,
    from_branch_id     int NOT NULL,
    to_branch_id       int NOT NULL,
    status             text NOT NULL CHECK (status IN
                       ('requested', 'approved', 'rejected', 'dispatched', 'received', 'received_with_discrepancy', 'closed', 'cancelled')),
    reason             text,
    approval_id        bigint REFERENCES motion.approval_request(id),
    tryton_shipment_id int,
    requested_by       bigint NOT NULL REFERENCES motion.app_user(id),
    requested_at       timestamptz NOT NULL DEFAULT now(),
    dispatched_by      bigint REFERENCES motion.app_user(id),
    dispatched_at      timestamptz,
    received_by        bigint REFERENCES motion.app_user(id),
    received_at        timestamptz,
    CHECK (from_branch_id <> to_branch_id)
);
CREATE TABLE motion.transfer_line (
    id             bigserial PRIMARY KEY,
    transfer_id    bigint NOT NULL REFERENCES motion.transfer(id),
    product_id     int NOT NULL,
    requested_qty  numeric(18, 3) NOT NULL CHECK (requested_qty > 0),
    dispatched_qty numeric(18, 3),
    received_qty   numeric(18, 3),
    damaged_qty    numeric(18, 3) NOT NULL DEFAULT 0,
    UNIQUE (transfer_id, product_id)
);

CREATE TABLE motion.discrepancy (
    id                bigserial PRIMARY KEY,
    kind              text NOT NULL CHECK (kind IN
                      ('transfer_shortage', 'transfer_damage', 'receipt_shortage', 'count_variance', 'sync_conflict', 'cash_variance')),
    branch_id         int NOT NULL,
    source_type       text NOT NULL,
    source_id         text NOT NULL,
    product_id        int,
    quantity          numeric(18, 3),
    amount            numeric(18, 2),
    detail            jsonb,
    status            text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'pending_approval', 'resolved')),
    resolution        text,
    resolution_note   text,
    approval_id       bigint REFERENCES motion.approval_request(id),
    created_by        bigint REFERENCES motion.app_user(id),
    created_at        timestamptz NOT NULL DEFAULT now(),
    resolved_by       bigint REFERENCES motion.app_user(id),
    resolved_at       timestamptz
);
CREATE INDEX discrepancy_open_idx ON motion.discrepancy (status, branch_id);

-- ---------------------------------------------------------------- offline edge sync
CREATE TABLE motion.edge_device (
    id           bigserial PRIMARY KEY,
    branch_id    int NOT NULL,
    name         text NOT NULL,
    token_hash   text NOT NULL UNIQUE,
    created_by   bigint NOT NULL REFERENCES motion.app_user(id),
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz,
    revoked_at   timestamptz
);

CREATE TABLE motion.sync_inbox (
    id             bigserial PRIMARY KEY,
    edge_device_id bigint NOT NULL REFERENCES motion.edge_device(id),
    event_id       uuid NOT NULL UNIQUE,
    kind           text NOT NULL,
    payload        jsonb NOT NULL,
    received_at    timestamptz NOT NULL DEFAULT now(),
    status         text NOT NULL CHECK (status IN ('posted', 'posted_with_conflict', 'rejected')),
    result         jsonb
);

-- ---------------------------------------------------------------- finance inputs
CREATE TABLE motion.investment (
    id          bigserial PRIMARY KEY,
    label       text NOT NULL,
    amount      numeric(18, 2) NOT NULL CHECK (amount > 0),
    invested_on date NOT NULL,
    branch_id   int,
    note        text,
    is_test_data boolean NOT NULL DEFAULT false,
    created_by  bigint REFERENCES motion.app_user(id),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE motion.insight_ack (
    insight_key text PRIMARY KEY,
    status      text NOT NULL CHECK (status IN ('acknowledged', 'dismissed')),
    note        text,
    by_user     bigint NOT NULL REFERENCES motion.app_user(id),
    at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE motion.app_setting (
    key        text PRIMARY KEY,
    value      jsonb NOT NULL,
    updated_by bigint REFERENCES motion.app_user(id),
    updated_at timestamptz NOT NULL DEFAULT now()
);
