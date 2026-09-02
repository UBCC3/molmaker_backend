-- Add the archive provider selected for each job.
--
-- Existing Orcinus jobs predate provider selection and are assigned to Garage.
-- The application explicitly sets this field for new jobs; the database default
-- remains S3 for compatibility with direct inserts and fresh-schema defaults.

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS archive_storage_service VARCHAR;

UPDATE jobs
SET archive_storage_service = 'garage'
WHERE archive_storage_service IS NULL;

ALTER TABLE jobs
    ALTER COLUMN archive_storage_service SET DEFAULT 's3',
    ALTER COLUMN archive_storage_service SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_jobs_archive_storage_service'
          AND conrelid = 'jobs'::regclass
    ) THEN
        ALTER TABLE jobs
            ADD CONSTRAINT ck_jobs_archive_storage_service
            CHECK (archive_storage_service IN ('s3', 'garage'));
    END IF;
END
$$;
