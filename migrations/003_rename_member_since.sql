-- Rename the user timestamp now that it records role and group changes.

BEGIN;

ALTER TABLE public.users
    RENAME COLUMN member_since TO role_or_group_updated_at;

UPDATE public.users
SET role_or_group_updated_at = NOW()
WHERE role_or_group_updated_at IS NULL;

ALTER TABLE public.users
    ALTER COLUMN role_or_group_updated_at SET DEFAULT NOW(),
    ALTER COLUMN role_or_group_updated_at SET NOT NULL;

COMMIT;
