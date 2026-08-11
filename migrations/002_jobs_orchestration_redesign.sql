-- Add the jobs API, orchestration state, and retained calculation inputs.
-- Run this after migrations/001_pr14_database_changes.sql.
-- It is safe to run this file again after it succeeds.

BEGIN;

ALTER TABLE public.jobs
    ADD COLUMN IF NOT EXISTS attempt_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS terminal_status character varying,
    ADD COLUMN IF NOT EXISTS cancel_requested boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS failure_reason character varying,
    ADD COLUMN IF NOT EXISTS failure_message text,
    ADD COLUMN IF NOT EXISTS optimization_type character varying;

CREATE TABLE IF NOT EXISTS public.job_inputs (
    job_id uuid PRIMARY KEY
        REFERENCES public.jobs(job_id) ON DELETE CASCADE,
    input_xyz text NOT NULL,
    keywords jsonb
);

-- Slurm IDs are internal identifiers, not numbers used for arithmetic. Keep
-- them as strings so the backend can preserve scheduler output exactly.

DO $$
DECLARE
    current_data_type text;
BEGIN
    SELECT data_type
    INTO current_data_type
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'jobs'
      AND column_name = 'slurm_id';

    IF current_data_type IS NULL THEN
        RAISE EXCEPTION 'jobs.slurm_id is missing';
    ELSIF current_data_type NOT IN ('text', 'character varying') THEN
        ALTER TABLE public.jobs
            ALTER COLUMN slurm_id TYPE character varying
            USING slurm_id::text;
    END IF;
END $$;

-- Preserve accepted legacy jobs when their Slurm ID is already known. A
-- pending row without a Slurm ID still needs the submission reconciler.

UPDATE public.jobs
SET status = CASE
    WHEN slurm_id IS NULL THEN 'submitting'
    ELSE 'submitted'
END
WHERE status = 'pending';

UPDATE public.jobs
SET status = 'failed',
    failure_reason = COALESCE(failure_reason, 'out_of_memory'),
    completed_at = COALESCE(completed_at, NOW())
WHERE status = 'out_of_memory';

UPDATE public.jobs
SET status = 'failed',
    failure_reason = COALESCE(failure_reason, 'timeout'),
    completed_at = COALESCE(completed_at, NOW())
WHERE status = 'timeout';

CREATE INDEX IF NOT EXISTS idx_jobs_orchestration_active
ON public.jobs(status, submitted_at, job_id)
WHERE status IN ('submitting', 'submitted', 'running', 'finalising');

COMMIT;
