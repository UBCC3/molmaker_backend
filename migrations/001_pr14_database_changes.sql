-- Migration for the backend changes in PR 14.
-- Run this against the database schema from main before deploying the new code.
-- It is safe to run this file again after it succeeds.

BEGIN;

-- Rename the timestamp first so the rest of the migration can use one name.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'users'
          AND column_name = 'member_since'
    ) THEN
        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'users'
              AND column_name = 'role_or_group_updated_at'
        ) THEN
            RAISE EXCEPTION
                'users has both member_since and role_or_group_updated_at';
        END IF;

        ALTER TABLE public.users
            RENAME COLUMN member_since TO role_or_group_updated_at;
    ELSIF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'users'
          AND column_name = 'role_or_group_updated_at'
    ) THEN
        RAISE EXCEPTION
            'users is missing both member_since and role_or_group_updated_at';
    END IF;
END $$;

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
  AND u.role_or_group_updated_at IS NOT NULL
  AND j.submitted_at >= u.role_or_group_updated_at;

UPDATE public.structures s
SET group_id = u.group_id
FROM public.users u
WHERE s.user_sub = u.user_sub
  AND s.group_id IS NULL
  AND u.group_id IS NOT NULL
  AND u.role_or_group_updated_at IS NOT NULL
  AND s.uploaded_at >= u.role_or_group_updated_at;

DO $$
BEGIN
    ALTER TABLE public.jobs
        DROP CONSTRAINT IF EXISTS ck_jobs_owner_present;
    ALTER TABLE public.jobs
        ADD CONSTRAINT ck_jobs_owner_present
        CHECK (is_deleted OR user_sub IS NOT NULL OR group_id IS NOT NULL);

    ALTER TABLE public.structures
        DROP CONSTRAINT IF EXISTS ck_structures_owner_present;
    ALTER TABLE public.structures
        ADD CONSTRAINT ck_structures_owner_present
        CHECK (is_deleted OR user_sub IS NOT NULL OR group_id IS NOT NULL);

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

WITH duplicate_tags AS (
    SELECT
        tag_id,
        first_value(tag_id) OVER (
            PARTITION BY user_sub, name
            ORDER BY tag_id::text
        ) AS canonical_tag_id
    FROM public.tags
)
INSERT INTO public.jobs_tags (job_id, tag_id)
SELECT jt.job_id, dt.canonical_tag_id
FROM public.jobs_tags jt
JOIN duplicate_tags dt ON jt.tag_id = dt.tag_id
WHERE dt.tag_id <> dt.canonical_tag_id
ON CONFLICT DO NOTHING;

WITH duplicate_tags AS (
    SELECT
        tag_id,
        first_value(tag_id) OVER (
            PARTITION BY user_sub, name
            ORDER BY tag_id::text
        ) AS canonical_tag_id
    FROM public.tags
)
DELETE FROM public.jobs_tags jt
USING duplicate_tags dt
WHERE jt.tag_id = dt.tag_id
  AND dt.tag_id <> dt.canonical_tag_id;

WITH duplicate_tags AS (
    SELECT
        tag_id,
        first_value(tag_id) OVER (
            PARTITION BY user_sub, name
            ORDER BY tag_id::text
        ) AS canonical_tag_id
    FROM public.tags
)
INSERT INTO public.structures_tags (structure_id, tag_id)
SELECT st.structure_id, dt.canonical_tag_id
FROM public.structures_tags st
JOIN duplicate_tags dt ON st.tag_id = dt.tag_id
WHERE dt.tag_id <> dt.canonical_tag_id
ON CONFLICT DO NOTHING;

WITH duplicate_tags AS (
    SELECT
        tag_id,
        first_value(tag_id) OVER (
            PARTITION BY user_sub, name
            ORDER BY tag_id::text
        ) AS canonical_tag_id
    FROM public.tags
)
DELETE FROM public.structures_tags st
USING duplicate_tags dt
WHERE st.tag_id = dt.tag_id
  AND dt.tag_id <> dt.canonical_tag_id;

WITH duplicate_tags AS (
    SELECT
        tag_id,
        first_value(tag_id) OVER (
            PARTITION BY user_sub, name
            ORDER BY tag_id::text
        ) AS canonical_tag_id
    FROM public.tags
)
DELETE FROM public.tags t
USING duplicate_tags dt
WHERE t.tag_id = dt.tag_id
  AND dt.tag_id <> dt.canonical_tag_id;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_tags_user_sub_name'
    ) THEN
        ALTER TABLE public.tags
            ADD CONSTRAINT uq_tags_user_sub_name
            UNIQUE (user_sub, name);
    END IF;
END $$;

-- Add the fields used by typed group membership requests.

ALTER TABLE public.requests
    ALTER COLUMN sender_sub DROP NOT NULL,
    ALTER COLUMN group_id DROP NOT NULL;

ALTER TABLE public.requests
    ADD COLUMN IF NOT EXISTS created_by_sub character varying,
    ADD COLUMN IF NOT EXISTS resolved_by_sub character varying,
    ADD COLUMN IF NOT EXISTS expires_at timestamp with time zone,
    ADD COLUMN IF NOT EXISTS resolved_at timestamp with time zone,
    ADD COLUMN IF NOT EXISTS sender_email_snapshot character varying,
    ADD COLUMN IF NOT EXISTS receiver_email_snapshot character varying,
    ADD COLUMN IF NOT EXISTS created_by_email_snapshot character varying,
    ADD COLUMN IF NOT EXISTS resolved_by_email_snapshot character varying,
    ADD COLUMN IF NOT EXISTS group_name_snapshot character varying;

UPDATE public.requests
SET created_by_sub = sender_sub
WHERE created_by_sub IS NULL;

UPDATE public.requests AS request
SET sender_email_snapshot = "user".email
FROM public.users AS "user"
WHERE request.sender_sub = "user".user_sub
  AND request.sender_email_snapshot IS NULL;

UPDATE public.requests AS request
SET receiver_email_snapshot = "user".email
FROM public.users AS "user"
WHERE request.receiver_sub = "user".user_sub
  AND request.receiver_email_snapshot IS NULL;

UPDATE public.requests AS request
SET created_by_email_snapshot = "user".email
FROM public.users AS "user"
WHERE request.created_by_sub = "user".user_sub
  AND request.created_by_email_snapshot IS NULL;

UPDATE public.requests AS request
SET resolved_by_email_snapshot = "user".email
FROM public.users AS "user"
WHERE request.resolved_by_sub = "user".user_sub
  AND request.resolved_by_email_snapshot IS NULL;

UPDATE public.requests AS request
SET group_name_snapshot = "group".name
FROM public.groups AS "group"
WHERE request.group_id = "group".group_id
  AND request.group_name_snapshot IS NULL;

UPDATE public.requests
SET requested_at = NOW()
WHERE requested_at IS NULL;

UPDATE public.requests
SET resolved_at = requested_at
WHERE status <> 'pending' AND resolved_at IS NULL;

UPDATE public.requests
SET expires_at = requested_at + INTERVAL '7 days'
WHERE expires_at IS NULL;

ALTER TABLE public.requests
    ALTER COLUMN requested_at SET DEFAULT NOW(),
    ALTER COLUMN requested_at SET NOT NULL,
    ALTER COLUMN expires_at SET DEFAULT (NOW() + INTERVAL '7 days'),
    ALTER COLUMN expires_at SET NOT NULL,
    ALTER COLUMN created_by_sub DROP NOT NULL;

ALTER TABLE public.requests
    DROP CONSTRAINT IF EXISTS requests_group_id_fkey,
    DROP CONSTRAINT IF EXISTS requests_receiver_sub_fkey,
    DROP CONSTRAINT IF EXISTS requests_sender_sub_fkey,
    DROP CONSTRAINT IF EXISTS requests_created_by_sub_fkey,
    DROP CONSTRAINT IF EXISTS requests_resolved_by_sub_fkey,
    DROP CONSTRAINT IF EXISTS fk_requests_created_by_sub,
    DROP CONSTRAINT IF EXISTS fk_requests_resolved_by_sub;

ALTER TABLE public.requests
    ADD CONSTRAINT requests_group_id_fkey
    FOREIGN KEY (group_id) REFERENCES public.groups(group_id)
    ON DELETE SET NULL;

ALTER TABLE public.requests
    ADD CONSTRAINT requests_receiver_sub_fkey
    FOREIGN KEY (receiver_sub) REFERENCES public.users(user_sub)
    ON DELETE SET NULL;

ALTER TABLE public.requests
    ADD CONSTRAINT requests_sender_sub_fkey
    FOREIGN KEY (sender_sub) REFERENCES public.users(user_sub)
    ON DELETE SET NULL;

ALTER TABLE public.requests
    ADD CONSTRAINT requests_created_by_sub_fkey
    FOREIGN KEY (created_by_sub) REFERENCES public.users(user_sub)
    ON DELETE SET NULL;

ALTER TABLE public.requests
    ADD CONSTRAINT requests_resolved_by_sub_fkey
    FOREIGN KEY (resolved_by_sub) REFERENCES public.users(user_sub)
    ON DELETE SET NULL;

-- Keep the oldest matching pending request before adding unique indexes.

WITH ranked_pending_requests AS (
    SELECT
        request_id,
        ROW_NUMBER() OVER (
            PARTITION BY
                request_type,
                group_id,
                CASE
                    WHEN request_type = 'invite' THEN receiver_sub
                    ELSE sender_sub
                END
            ORDER BY requested_at, request_id::text
        ) AS duplicate_number
    FROM public.requests
    WHERE status = 'pending'
      AND (
          (request_type = 'invite' AND receiver_sub IS NOT NULL)
          OR (
              request_type IN ('join_request', 'demember_request')
              AND sender_sub IS NOT NULL
          )
      )
)
UPDATE public.requests AS request
SET
    status = 'cancelled',
    resolved_at = COALESCE(request.resolved_at, NOW()),
    resolved_by_sub = NULL
FROM ranked_pending_requests AS ranked
WHERE request.request_id = ranked.request_id
  AND ranked.duplicate_number > 1;

CREATE UNIQUE INDEX IF NOT EXISTS uq_requests_pending_invite
ON public.requests(group_id, receiver_sub)
WHERE status = 'pending' AND request_type = 'invite';

CREATE UNIQUE INDEX IF NOT EXISTS uq_requests_pending_join
ON public.requests(group_id, sender_sub)
WHERE status = 'pending' AND request_type = 'join_request';

CREATE UNIQUE INDEX IF NOT EXISTS uq_requests_pending_demember
ON public.requests(group_id, sender_sub)
WHERE status = 'pending' AND request_type = 'demember_request';

CREATE INDEX IF NOT EXISTS idx_requests_created_by_status
ON public.requests(created_by_sub, status);

CREATE INDEX IF NOT EXISTS idx_requests_status_expires_at
ON public.requests(status, expires_at);

-- Finish setting the rules for the renamed timestamp.

UPDATE public.users
SET role_or_group_updated_at = NOW()
WHERE role_or_group_updated_at IS NULL;

ALTER TABLE public.users
    ALTER COLUMN role_or_group_updated_at SET DEFAULT NOW(),
    ALTER COLUMN role_or_group_updated_at SET NOT NULL;

COMMIT;
