-- Manual migration for the typed group membership request flow.

BEGIN;

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
SET expires_at = COALESCE(expires_at, requested_at + INTERVAL '7 days')
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

COMMIT;
