-- Codex stage 2 (H2, H3).
--
-- H2: one idempotency contract for every money-moving command that a client may
-- retry (POS sale, refund pay-out, bank deposit). The key is scoped to the
-- operation and the calling user; the fingerprint is a hash of the canonical
-- request. The row is written in the same transaction as the business result,
-- so it either exists with its result or not at all; a concurrent duplicate
-- waits on the primary key, is retried with a fresh snapshot and replays.
CREATE TABLE motion.operation (
    operation    text NOT NULL,
    actor_id     bigint NOT NULL REFERENCES motion.app_user(id),
    key          text NOT NULL CHECK (length(key) BETWEEN 8 AND 120),
    fingerprint  text NOT NULL,
    result_type  text NOT NULL,
    result_id    text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (operation, actor_id, key)
);

-- H3: per-line original gross / VAT and what has been refunded so far, so the
-- final partial refund closes the line exactly (no rounding residue) and a
-- refund reverses the VAT of the original sale, not today's tax rules.
ALTER TABLE motion.pos_sale_line
    ADD COLUMN line_gross     numeric(18, 2),
    ADD COLUMN line_tax       numeric(18, 2),
    ADD COLUMN tax_ids        int[],
    ADD COLUMN refunded_gross numeric(18, 2) NOT NULL DEFAULT 0,
    ADD COLUMN refunded_tax   numeric(18, 2) NOT NULL DEFAULT 0;

-- Banker's rounding to whole shillings, matching Tryton's currency rounding.
CREATE FUNCTION motion.round_half_even(x numeric) RETURNS numeric
    LANGUAGE sql IMMUTABLE AS
$$ SELECT CASE WHEN abs(x - trunc(x)) = 0.5 THEN 2 * round(x / 2) ELSE round(x) END $$;

-- Backfill existing sale lines from the Tryton line (gross and untaxed amounts).
-- On a fresh database the Tryton tables do not exist yet and there is nothing to fill.
DO $$
BEGIN
    IF to_regclass('public.sale_point_sale_line') IS NOT NULL THEN
        UPDATE motion.pos_sale_line l
        SET line_gross = motion.round_half_even(l.quantity * tl.unit_gross_price),
            line_tax   = motion.round_half_even(l.quantity * tl.unit_gross_price)
                       - motion.round_half_even(l.quantity * tl.unit_list_price)
        FROM public.sale_point_sale_line tl
        WHERE tl.id = l.tryton_line_id;

        UPDATE motion.pos_sale_line o
        SET refunded_gross = r.gross, refunded_tax = r.tax
        FROM (SELECT refund_of_line, -sum(line_gross) AS gross, -sum(line_tax) AS tax
              FROM motion.pos_sale_line WHERE refund_of_line IS NOT NULL GROUP BY 1) r
        WHERE r.refund_of_line = o.id;
    END IF;
END
$$;

ALTER TABLE motion.pos_sale_line
    ALTER COLUMN line_gross SET NOT NULL,
    ALTER COLUMN line_tax SET NOT NULL,
    ADD CONSTRAINT pos_sale_line_refund_gross_bound CHECK (quantity < 0 OR refunded_gross <= line_gross);

-- H10: checkout and refund bump this counter on the open shift, so they serialize
-- with shift close (a close never counts cash while a sale is half-posted).
ALTER TABLE motion.shift ADD COLUMN activity_seq bigint NOT NULL DEFAULT 0;
