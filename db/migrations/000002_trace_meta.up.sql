ALTER TABLE query_traces
    ADD COLUMN status text NOT NULL DEFAULT 'answered',
    ADD COLUMN reason text,
    ADD COLUMN meta   jsonb NOT NULL DEFAULT '{}';
CREATE INDEX query_traces_created_idx ON query_traces (created_at DESC);
