-- Manual migration for persisted job/structure group ownership.

BEGIN;

ALTER TABLE public.jobs
    ADD COLUMN IF NOT EXISTS group_id uuid;

ALTER TABLE public.jobs
    ALTER COLUMN user_sub DROP NOT NULL;

ALTER TABLE public.structures
    ADD COLUMN IF NOT EXISTS group_id uuid,
    ADD COLUMN IF NOT EXISTS is_public boolean NOT NULL DEFAULT false;

ALTER TABLE public.structures
    ALTER COLUMN user_sub DROP NOT NULL;

ALTER TABLE public.requests
    ADD COLUMN IF NOT EXISTS request_type character varying NOT NULL DEFAULT 'invite';

ALTER TABLE public.requests
    ALTER COLUMN receiver_sub DROP NOT NULL;

UPDATE public.jobs j
SET group_id = u.group_id
FROM public.users u
WHERE j.user_sub = u.user_sub
  AND j.group_id IS NULL
  AND u.group_id IS NOT NULL
  AND u.member_since IS NOT NULL
  AND j.submitted_at >= u.member_since;

UPDATE public.structures s
SET group_id = u.group_id
FROM public.users u
WHERE s.user_sub = u.user_sub
  AND s.group_id IS NULL
  AND u.group_id IS NOT NULL
  AND u.member_since IS NOT NULL
  AND s.uploaded_at >= u.member_since;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_jobs_owner_present'
    ) THEN
        ALTER TABLE public.jobs
            ADD CONSTRAINT ck_jobs_owner_present
            CHECK (user_sub IS NOT NULL OR group_id IS NOT NULL);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_structures_owner_present'
    ) THEN
        ALTER TABLE public.structures
            ADD CONSTRAINT ck_structures_owner_present
            CHECK (user_sub IS NOT NULL OR group_id IS NOT NULL);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_jobs_group_id'
    ) THEN
        ALTER TABLE public.jobs
            ADD CONSTRAINT fk_jobs_group_id
            FOREIGN KEY (group_id) REFERENCES public.groups(group_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_structures_group_id'
    ) THEN
        ALTER TABLE public.structures
            ADD CONSTRAINT fk_structures_group_id
            FOREIGN KEY (group_id) REFERENCES public.groups(group_id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_users_group_role
ON public.users(group_id, role);

CREATE INDEX IF NOT EXISTS idx_jobs_user_active_submitted
ON public.jobs(user_sub, is_deleted, submitted_at DESC);

CREATE INDEX IF NOT EXISTS idx_jobs_group_active_submitted
ON public.jobs(group_id, is_deleted, submitted_at DESC);

CREATE INDEX IF NOT EXISTS idx_structures_user_active_uploaded
ON public.structures(user_sub, is_deleted, uploaded_at DESC);

CREATE INDEX IF NOT EXISTS idx_structures_group_active_uploaded
ON public.structures(group_id, is_deleted, uploaded_at DESC);

CREATE INDEX IF NOT EXISTS idx_requests_receiver_status
ON public.requests(receiver_sub, status);

CREATE INDEX IF NOT EXISTS idx_requests_sender_status
ON public.requests(sender_sub, status);

CREATE INDEX IF NOT EXISTS idx_requests_group_status_type
ON public.requests(group_id, status, request_type);

COMMIT;
