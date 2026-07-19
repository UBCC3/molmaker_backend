-- Small copy of the main database schema used to test the PR 14 migration.

CREATE TABLE public.groups (
    group_id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying NOT NULL,
    CONSTRAINT groups_pkey PRIMARY KEY (group_id),
    CONSTRAINT groups_name_key UNIQUE (name)
);

CREATE TABLE public.users (
    user_sub text NOT NULL,
    email text NOT NULL,
    role text DEFAULT 'member'::text NOT NULL,
    group_id uuid,
    member_since timestamp with time zone,
    CONSTRAINT users_pkey PRIMARY KEY (user_sub),
    CONSTRAINT users_email_key UNIQUE (email),
    CONSTRAINT users_group_id_fkey
        FOREIGN KEY (group_id) REFERENCES public.groups(group_id)
);

CREATE TABLE public.jobs (
    job_id uuid NOT NULL,
    filename text NOT NULL,
    status text NOT NULL,
    calculation_type text NOT NULL,
    method text NOT NULL,
    basis_set text NOT NULL,
    submitted_at timestamp with time zone NOT NULL,
    completed_at timestamp with time zone,
    user_sub text,
    job_name text,
    slurm_id integer,
    charge integer,
    multiplicity integer,
    job_notes text,
    runtime interval,
    is_deleted boolean DEFAULT false NOT NULL,
    is_public boolean DEFAULT false NOT NULL,
    is_uploaded boolean DEFAULT false NOT NULL,
    CONSTRAINT jobs_pkey PRIMARY KEY (job_id),
    CONSTRAINT fk_jobs_user_sub
        FOREIGN KEY (user_sub) REFERENCES public.users(user_sub)
);

CREATE TABLE public.structures (
    structure_id uuid NOT NULL,
    user_sub text NOT NULL,
    name text NOT NULL,
    location text NOT NULL,
    notes text,
    uploaded_at timestamp without time zone NOT NULL,
    is_deleted boolean DEFAULT false NOT NULL,
    formula text NOT NULL,
    CONSTRAINT structures_pkey PRIMARY KEY (structure_id),
    CONSTRAINT fk_structures_user_sub
        FOREIGN KEY (user_sub) REFERENCES public.users(user_sub)
);

CREATE TABLE public.requests (
    request_id uuid NOT NULL,
    status character varying NOT NULL,
    requested_at timestamp with time zone,
    sender_sub character varying NOT NULL,
    receiver_sub character varying NOT NULL,
    group_id uuid NOT NULL,
    CONSTRAINT requests_pkey PRIMARY KEY (request_id),
    CONSTRAINT requests_sender_sub_fkey
        FOREIGN KEY (sender_sub) REFERENCES public.users(user_sub),
    CONSTRAINT requests_receiver_sub_fkey
        FOREIGN KEY (receiver_sub) REFERENCES public.users(user_sub),
    CONSTRAINT requests_group_id_fkey
        FOREIGN KEY (group_id) REFERENCES public.groups(group_id)
);

CREATE TABLE public.tags (
    tag_id uuid NOT NULL,
    user_sub text NOT NULL,
    name text NOT NULL,
    CONSTRAINT tags_pkey PRIMARY KEY (tag_id)
);

CREATE TABLE public.jobs_structures (
    job_id uuid NOT NULL,
    structure_id uuid NOT NULL,
    CONSTRAINT jobs_structures_pkey PRIMARY KEY (job_id, structure_id),
    CONSTRAINT jobs_structures_job_id_fkey
        FOREIGN KEY (job_id) REFERENCES public.jobs(job_id) ON DELETE CASCADE,
    CONSTRAINT jobs_structures_structure_id_fkey
        FOREIGN KEY (structure_id) REFERENCES public.structures(structure_id)
        ON DELETE CASCADE
);

CREATE TABLE public.jobs_tags (
    job_id uuid NOT NULL,
    tag_id uuid NOT NULL,
    CONSTRAINT jobs_tags_pkey PRIMARY KEY (job_id, tag_id),
    CONSTRAINT jobs_tags_job_id_fkey
        FOREIGN KEY (job_id) REFERENCES public.jobs(job_id) ON DELETE CASCADE,
    CONSTRAINT jobs_tags_tag_id_fkey
        FOREIGN KEY (tag_id) REFERENCES public.tags(tag_id) ON DELETE CASCADE
);

CREATE TABLE public.structures_tags (
    structure_id uuid NOT NULL,
    tag_id uuid NOT NULL,
    CONSTRAINT structures_tags_pkey PRIMARY KEY (structure_id, tag_id),
    CONSTRAINT structures_tags_structure_id_fkey
        FOREIGN KEY (structure_id) REFERENCES public.structures(structure_id)
        ON DELETE CASCADE,
    CONSTRAINT structures_tags_tag_id_fkey
        FOREIGN KEY (tag_id) REFERENCES public.tags(tag_id) ON DELETE CASCADE
);

INSERT INTO public.groups (group_id, name) VALUES
    ('00000000-0000-0000-0000-000000000001', 'First group'),
    ('00000000-0000-0000-0000-000000000002', 'Second group');

INSERT INTO public.users (user_sub, email, role, group_id, member_since) VALUES
    (
        'auth0|owner',
        'owner@example.com',
        'group_admin',
        '00000000-0000-0000-0000-000000000001',
        '2025-01-10 00:00:00+00'
    ),
    (
        'auth0|target',
        'target@example.com',
        'member',
        NULL,
        NULL
    ),
    (
        'auth0|other',
        'other@example.com',
        'member',
        NULL,
        '2025-01-05 00:00:00+00'
    );

INSERT INTO public.jobs (
    job_id,
    filename,
    status,
    calculation_type,
    method,
    basis_set,
    submitted_at,
    user_sub,
    is_deleted,
    is_public,
    is_uploaded
) VALUES
    (
        '10000000-0000-0000-0000-000000000001',
        'before.xyz',
        'completed',
        'energy',
        'hf',
        'sto-3g',
        '2025-01-01 00:00:00+00',
        'auth0|owner',
        false,
        false,
        false
    ),
    (
        '10000000-0000-0000-0000-000000000002',
        'after.xyz',
        'completed',
        'energy',
        'hf',
        'sto-3g',
        '2025-02-01 00:00:00+00',
        'auth0|owner',
        false,
        false,
        false
    );

INSERT INTO public.structures (
    structure_id,
    user_sub,
    name,
    location,
    uploaded_at,
    is_deleted,
    formula
) VALUES
    (
        '20000000-0000-0000-0000-000000000001',
        'auth0|owner',
        'Before membership',
        's3://test/before.xyz',
        '2025-01-01 00:00:00',
        false,
        'H2O'
    ),
    (
        '20000000-0000-0000-0000-000000000002',
        'auth0|owner',
        'After membership',
        's3://test/after.xyz',
        '2025-02-01 00:00:00',
        false,
        'H2O'
    );

INSERT INTO public.requests (
    request_id,
    status,
    requested_at,
    sender_sub,
    receiver_sub,
    group_id
) VALUES
    (
        '30000000-0000-0000-0000-000000000001',
        'pending',
        '2025-01-01 00:00:00+00',
        'auth0|owner',
        'auth0|target',
        '00000000-0000-0000-0000-000000000001'
    ),
    (
        '30000000-0000-0000-0000-000000000002',
        'pending',
        '2025-01-02 00:00:00+00',
        'auth0|owner',
        'auth0|target',
        '00000000-0000-0000-0000-000000000001'
    ),
    (
        '30000000-0000-0000-0000-000000000003',
        'approved',
        NULL,
        'auth0|owner',
        'auth0|other',
        '00000000-0000-0000-0000-000000000001'
    );

INSERT INTO public.tags (tag_id, user_sub, name) VALUES
    (
        '40000000-0000-0000-0000-000000000001',
        'auth0|owner',
        'duplicate'
    ),
    (
        '40000000-0000-0000-0000-000000000002',
        'auth0|owner',
        'duplicate'
    );

INSERT INTO public.jobs_tags (job_id, tag_id) VALUES
    (
        '10000000-0000-0000-0000-000000000002',
        '40000000-0000-0000-0000-000000000001'
    ),
    (
        '10000000-0000-0000-0000-000000000002',
        '40000000-0000-0000-0000-000000000002'
    );

INSERT INTO public.structures_tags (structure_id, tag_id) VALUES
    (
        '20000000-0000-0000-0000-000000000002',
        '40000000-0000-0000-0000-000000000001'
    ),
    (
        '20000000-0000-0000-0000-000000000002',
        '40000000-0000-0000-0000-000000000002'
    );
