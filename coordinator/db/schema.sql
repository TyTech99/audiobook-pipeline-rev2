CREATE TABLE IF NOT EXISTS schema_version (
    version integer PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS nodes (
    node_id text PRIMARY KEY,
    display_name text NOT NULL DEFAULT '',
    state text NOT NULL,
    key_hash text,
    previous_key_hash text,
    previous_key_expires_at timestamptz,
    allowed_queues text[] NOT NULL,
    effective_queues text[] NOT NULL DEFAULT '{}',
    hardware jsonb,
    working_hours jsonb,
    last_heartbeat_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS nodes_key_hash_idx ON nodes (key_hash);
CREATE INDEX IF NOT EXISTS nodes_previous_key_hash_idx ON nodes (previous_key_hash);

CREATE TABLE IF NOT EXISTS probes (
    probe_id uuid PRIMARY KEY,
    node_id text NOT NULL REFERENCES nodes (node_id),
    worker_type text NOT NULL,
    status text NOT NULL,
    worker_id uuid,
    detail text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workers (
    worker_id uuid PRIMARY KEY,
    node_id text NOT NULL REFERENCES nodes (node_id),
    worker_type text NOT NULL,
    status text NOT NULL,
    probe boolean NOT NULL DEFAULT false,
    probe_id uuid,
    current_job text,
    last_heartbeat_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO schema_version (version) VALUES (1) ON CONFLICT DO NOTHING;
