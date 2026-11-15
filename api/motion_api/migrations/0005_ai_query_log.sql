-- Ask Motion AI: auditable query log (who asked what, which read-only tools ran,
-- what was answered, model, tokens, latency). Retention: purge rows older than
-- 90 days (documented operational task; not automated yet).
CREATE TABLE motion.ai_query (
    id          bigserial PRIMARY KEY,
    user_id     bigint NOT NULL REFERENCES motion.app_user(id),
    at          timestamptz NOT NULL DEFAULT now(),
    question    text NOT NULL,
    tools       jsonb NOT NULL DEFAULT '[]',
    answer      text,
    model       text,
    provider    text,
    prompt_tokens     int,
    completion_tokens int,
    latency_ms  int,
    status      text NOT NULL CHECK (status IN ('answered', 'fallback', 'error', 'rate_limited'))
);
CREATE INDEX ai_query_user_at_idx ON motion.ai_query (user_id, at DESC);
