-- Store structures and frontend-facing job results in PostgreSQL.
-- Run this after migrations/002_jobs_orchestration_redesign.sql.
-- It is safe to run this file again after it succeeds.

BEGIN;

ALTER TABLE public.structures
    ADD COLUMN IF NOT EXISTS content text,
    ADD COLUMN IF NOT EXISTS thumbnail bytea,
    ADD COLUMN IF NOT EXISTS thumbnail_media_type text,
    ALTER COLUMN location DROP NOT NULL;

CREATE TABLE IF NOT EXISTS public.job_results (
    job_id uuid PRIMARY KEY
        REFERENCES public.jobs(job_id) ON DELETE CASCADE,
    result jsonb,
    error jsonb,
    artifacts jsonb NOT NULL DEFAULT '{}'::jsonb
);

COMMIT;
