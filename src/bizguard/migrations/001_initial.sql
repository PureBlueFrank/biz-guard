CREATE TABLE IF NOT EXISTS bizguard_approvals (
    change_context_id TEXT NOT NULL,
    policy_revision TEXT NOT NULL,
    approver_set TEXT NOT NULL,
    payload TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (change_context_id, policy_revision, approver_set)
);

CREATE INDEX IF NOT EXISTS bizguard_approvals_context_updated_idx
    ON bizguard_approvals (change_context_id, policy_revision, updated_at DESC);

CREATE TABLE IF NOT EXISTS bizguard_approval_audit (
    seq BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    change_context_id TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS bizguard_approval_audit_context_seq_idx
    ON bizguard_approval_audit (change_context_id, seq);

CREATE TABLE IF NOT EXISTS bizguard_change_context (
    id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
