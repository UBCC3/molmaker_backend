-- Snapshot each job's requested Slurm runtime and memory with its immutable input.
--
-- Existing queued jobs retain NULL values and use the configured backend defaults
-- when reconciled. Every job created by the updated API stores resolved values.

ALTER TABLE job_inputs
    ADD COLUMN IF NOT EXISTS time_limit_minutes INTEGER,
    ADD COLUMN IF NOT EXISTS memory_mb INTEGER;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_job_inputs_time_limit_positive'
          AND conrelid = 'job_inputs'::regclass
    ) THEN
        ALTER TABLE job_inputs
            ADD CONSTRAINT ck_job_inputs_time_limit_positive
            CHECK (time_limit_minutes IS NULL OR time_limit_minutes > 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_job_inputs_memory_positive'
          AND conrelid = 'job_inputs'::regclass
    ) THEN
        ALTER TABLE job_inputs
            ADD CONSTRAINT ck_job_inputs_memory_positive
            CHECK (memory_mb IS NULL OR memory_mb > 0);
    END IF;
END
$$;
