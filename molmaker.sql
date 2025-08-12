--
-- PostgreSQL database dump
--

-- Dumped from database version 14.18 (Postgres.app)
-- Dumped by pg_dump version 14.18 (Homebrew)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: groups; Type: TABLE; Schema: public; Owner: sparshtrivedy
--

CREATE TABLE public.groups (
    group_id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying NOT NULL
);


ALTER TABLE public.groups OWNER TO sparshtrivedy;

--
-- Name: jobs; Type: TABLE; Schema: public; Owner: sparshtrivedy
--

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
    is_uploaded boolean DEFAULT false NOT NULL
);


ALTER TABLE public.jobs OWNER TO sparshtrivedy;

--
-- Name: jobs_structures; Type: TABLE; Schema: public; Owner: sparshtrivedy
--

CREATE TABLE public.jobs_structures (
    job_id uuid NOT NULL,
    structure_id uuid NOT NULL
);


ALTER TABLE public.jobs_structures OWNER TO sparshtrivedy;

--
-- Name: jobs_tags; Type: TABLE; Schema: public; Owner: sparshtrivedy
--

CREATE TABLE public.jobs_tags (
    job_id uuid NOT NULL,
    tag_id uuid NOT NULL
);


ALTER TABLE public.jobs_tags OWNER TO sparshtrivedy;

--
-- Name: requests; Type: TABLE; Schema: public; Owner: sparshtrivedy
--

CREATE TABLE public.requests (
    request_id uuid NOT NULL,
    status character varying NOT NULL,
    requested_at timestamp with time zone,
    sender_sub character varying NOT NULL,
    receiver_sub character varying NOT NULL,
    group_id uuid NOT NULL
);


ALTER TABLE public.requests OWNER TO sparshtrivedy;

--
-- Name: structures; Type: TABLE; Schema: public; Owner: sparshtrivedy
--

CREATE TABLE public.structures (
    structure_id uuid NOT NULL,
    user_sub text NOT NULL,
    name text NOT NULL,
    location text NOT NULL,
    notes text,
    uploaded_at timestamp without time zone NOT NULL,
    is_deleted boolean DEFAULT false NOT NULL,
    formula text NOT NULL
);


ALTER TABLE public.structures OWNER TO sparshtrivedy;

--
-- Name: structures_tags; Type: TABLE; Schema: public; Owner: sparshtrivedy
--

CREATE TABLE public.structures_tags (
    structure_id uuid NOT NULL,
    tag_id uuid NOT NULL
);


ALTER TABLE public.structures_tags OWNER TO sparshtrivedy;

--
-- Name: tags; Type: TABLE; Schema: public; Owner: sparshtrivedy
--

CREATE TABLE public.tags (
    tag_id uuid NOT NULL,
    user_sub text NOT NULL,
    name text NOT NULL
);


ALTER TABLE public.tags OWNER TO sparshtrivedy;

--
-- Name: users; Type: TABLE; Schema: public; Owner: sparshtrivedy
--

CREATE TABLE public.users (
    user_sub text NOT NULL,
    email text NOT NULL,
    role text DEFAULT 'member'::text NOT NULL,
    group_id uuid,
    member_since timestamp with time zone
);


ALTER TABLE public.users OWNER TO sparshtrivedy;

--
-- Data for Name: groups; Type: TABLE DATA; Schema: public; Owner: sparshtrivedy
--

COPY public.groups (group_id, name) FROM stdin;
2ba29864-e9c7-47b3-a718-3e854857ce57	group2
0e141968-a172-4a44-a730-09461d5b84b1	group3
e09abdef-b0cf-4b8d-ac14-bcb591724b6c	group4
\.


--
-- Data for Name: jobs; Type: TABLE DATA; Schema: public; Owner: sparshtrivedy
--

COPY public.jobs (job_id, filename, status, calculation_type, method, basis_set, submitted_at, completed_at, user_sub, job_name, slurm_id, charge, multiplicity, job_notes, runtime, is_deleted, is_public) FROM stdin;
c3383ec3-8f50-4162-90c8-7c6bf6ddda18	5b8c773c-d2a3-47b7-b543-2a21a5e19698.xyz	completed	energy	scf	sto-3g	2025-06-18 13:59:49.742871-07	2025-06-18 14:03:51.625857-07	auth0|681d382c228898b5ba13b7be	new-Job	56545639	0	1	notes for job	00:01:08	f	f
32e590ed-f5c3-42f2-aa79-3656a86a4411	molecule.xyz	completed	orbitals	scf	6-31G\\(d\\)	2025-06-30 11:52:46.770272-07	2025-06-30 11:53:08.680474-07	auth0|681d382c228898b5ba13b7be	mol_orb_job	56958446	0	1	noteson the job	00:00:06	f	f
78497fab-a344-46fa-b7dd-819d5efb66ce	b4bf5356-df5a-4430-9582-9d8d3843bc64.xyz	failed	orbitals	scf	sto-3g	2025-06-30 12:37:14.755048-07	2025-06-30 12:41:17.128239-07	auth0|681d382c228898b5ba13b7be	new_job	56961971	0	1	\N	00:00:15	f	f
dc493d13-862e-4478-80e3-21eaeec77fb9	molecule.xyz	failed	orbitals	scf	6-31G\\(d\\)	2025-06-30 13:44:50.409067-07	2025-06-30 13:45:07.079206-07	auth0|681d382c228898b5ba13b7be	name_job	56965221	0	1	\N	00:00:16	f	f
4476af47-9849-4011-b324-c20953e27c31	molecule.xyz	completed	orbitals	scf	6-31G\\(d\\)	2025-06-30 13:53:53.179195-07	2025-06-30 13:59:08.656636-07	auth0|681d382c228898b5ba13b7be	job_new	56965835	0	1	\N	00:00:29	f	f
802242c9-813e-45ac-9ec3-4c88fac18418	7a3562a1-67a9-44c1-9d2e-06d08abfb20f.xyz	completed	energy	scf	sto-3g	2025-06-16 11:09:52.676169-07	2025-06-16 11:13:10.318966-07	auth0|681d382c228898b5ba13b7be	job_c	56483685	0	1	\N	00:01:33	f	f
9fd9c8c3-b48c-4321-a95e-5b34eb0eafeb	7a3562a1-67a9-44c1-9d2e-06d08abfb20f.xyz	cancelled	energy	scf	sto-3g	2025-06-16 10:55:07.989799-07	2025-06-16 11:21:06.536421-07	auth0|681d382c228898b5ba13b7be	name	56483201	0	1	note	00:00:00	f	f
7127926a-2d9c-4134-bbf1-af558c10e8be	1010f356-ac6d-4ed4-a83d-19c0cd2d4c15.xyz	completed	energy	scf	sto-3g	2025-06-12 13:04:03.987939-07	2025-06-12 13:05:45.832136-07	auth0|681d382c228898b5ba13b7be	job	56445844	0	1	note	00:01:08	f	f
fc4834ad-3180-4462-9121-0b33af302823	molecule (2).xyz	completed	energy	scf	sto-3g	2025-07-24 00:39:35.457181-07	2025-07-24 00:40:10.088334-07	auth0|686ffc7aa0025875955dae19	member_job	57638800	0	1	\N	00:00:32	f	f
a39b4f4d-2a81-48a3-ac85-dd87e75b07cd	water-4-vib.xyz	cancelled	energy	scf	sto-3g	2025-07-31 11:21:15.431832-07	2025-08-06 22:39:22.135125-07	auth0|681d382c228898b5ba13b7be	testing_member	57733776	0	1	\N	00:00:00	f	t
eb2c127f-33aa-4525-8a9e-e6f3dcd17d0b	water-4-vib.xyz	cancelled	energy	scf	sto-3g	2025-07-31 10:12:15.55166-07	2025-08-06 22:39:22.155026-07	auth0|681d382c228898b5ba13b7be	new_job	57733685	0	1	notes	00:00:00	f	f
da65463b-c301-45e4-83f1-0871227b2042	water-4-vib.xyz	cancelled	energy	scf	sto-3g	2025-07-31 11:16:05.769882-07	2025-08-06 22:39:22.15807-07	auth0|681d382c228898b5ba13b7be	member_job	57733767	0	1	\N	00:00:00	f	t
6b74f54c-a915-4daf-a331-fb85924b35c6	water-4-vib.xyz	cancelled	energy	scf	sto-3g	2025-07-31 10:46:42.097774-07	2025-08-06 22:39:22.295614-07	auth0|681d382c228898b5ba13b7be	job_after	57733725	0	1	\N	00:00:00	f	t
\.


--
-- Data for Name: jobs_structures; Type: TABLE DATA; Schema: public; Owner: sparshtrivedy
--

COPY public.jobs_structures (job_id, structure_id) FROM stdin;
c3383ec3-8f50-4162-90c8-7c6bf6ddda18	5b8c773c-d2a3-47b7-b543-2a21a5e19698
78497fab-a344-46fa-b7dd-819d5efb66ce	b4bf5356-df5a-4430-9582-9d8d3843bc64
\.


--
-- Data for Name: jobs_tags; Type: TABLE DATA; Schema: public; Owner: sparshtrivedy
--

COPY public.jobs_tags (job_id, tag_id) FROM stdin;
7127926a-2d9c-4134-bbf1-af558c10e8be	a3eb6c5d-82d2-45db-89e3-0753fc11d0bf
c3383ec3-8f50-4162-90c8-7c6bf6ddda18	92d1dc3c-f6bc-429c-be29-4ae99c64c73d
\.


--
-- Data for Name: requests; Type: TABLE DATA; Schema: public; Owner: sparshtrivedy
--

COPY public.requests (request_id, status, requested_at, sender_sub, receiver_sub, group_id) FROM stdin;
a8cc679c-2354-4a77-8e9d-378de58f8ff9	approved	2025-07-24 12:59:08.642048-07	auth0|686ffc7aa0025875955dae19	auth0|686f5a35f24ed5b3e3b966ea	2ba29864-e9c7-47b3-a718-3e854857ce57
98bec210-e1e6-4e1d-bade-a3f8d32fe0f8	pending	2025-07-29 16:19:00.833823-07	auth0|686ffc7aa0025875955dae19	auth0|6876a9bc512247093911921c	2ba29864-e9c7-47b3-a718-3e854857ce57
4ab24dff-0e53-465a-9864-133b6c7853f7	approved	2025-07-31 10:03:05.833293-07	auth0|686ffc7aa0025875955dae19	auth0|686ffc1ea0025875955dadfe	2ba29864-e9c7-47b3-a718-3e854857ce57
\.


--
-- Data for Name: structures; Type: TABLE DATA; Schema: public; Owner: sparshtrivedy
--

COPY public.structures (structure_id, user_sub, name, location, notes, uploaded_at, is_deleted, formula) FROM stdin;
5b8c773c-d2a3-47b7-b543-2a21a5e19698	auth0|681d382c228898b5ba13b7be	name	s3://molmaker/structures/5b8c773c-d2a3-47b7-b543-2a21a5e19698.xyz		2025-06-16 13:03:07.444752	f	C8H12Cr2O7(-2)
b4bf5356-df5a-4430-9582-9d8d3843bc64	auth0|681d382c228898b5ba13b7be	long	s3://molmaker/structures/b4bf5356-df5a-4430-9582-9d8d3843bc64.xyz		2025-06-18 19:39:59.605488	f	Nb2O2
e3962179-9245-494e-93bd-964de08882e5	auth0|681d382c228898b5ba13b7be	new_tes	s3://molmaker/structures/e3962179-9245-494e-93bd-964de08882e5.xyz	tesng	2025-07-08 12:09:46.52906	f	Unknown formula
5453611c-ae5e-46df-aa8d-bf49d770cbc9	auth0|681d382c228898b5ba13b7be	xyz_orb	s3://molmaker/structures/5453611c-ae5e-46df-aa8d-bf49d770cbc9.xyz	notes	2025-07-08 12:12:34.665828	f	Unknown formula
03ee2406-af44-430d-9347-8b632a4ca67d	auth0|681d382c228898b5ba13b7be	new_te	s3://molmaker/structures/03ee2406-af44-430d-9347-8b632a4ca67d.xyz	tesng	2025-07-08 12:08:47.988854	t	Unknown formula
2f56c8d3-2cca-45d8-9d93-6dc2e3d90f37	auth0|681d382c228898b5ba13b7be	orb_res	s3://molmaker/structures/2f56c8d3-2cca-45d8-9d93-6dc2e3d90f37.xyz	notes ont his	2025-07-08 11:54:51.513691	t	Unknown formula
22f14e5d-02d7-4ff3-a7e4-e31edd05c98a	auth0|681d382c228898b5ba13b7be	name_res	s3://molmaker/structures/22f14e5d-02d7-4ff3-a7e4-e31edd05c98a.xyz	note	2025-07-08 11:52:04.298742	t	Unknown formula
c44bee2d-b59e-4b70-b98f-af64d25b2811	auth0|681d382c228898b5ba13b7be	some_struct	s3://molmaker/structures/c44bee2d-b59e-4b70-b98f-af64d25b2811.xyz	structure notes here	2025-07-08 14:59:27.002324	f	Unknown formula
6a08e1d0-15ea-4f87-a0f8-13ecf231c4e6	auth0|681d382c228898b5ba13b7be	water	s3://molmaker/structures/6a08e1d0-15ea-4f87-a0f8-13ecf231c4e6.xyz		2025-08-07 22:53:53.302251	f	H2O
\.


--
-- Data for Name: structures_tags; Type: TABLE DATA; Schema: public; Owner: sparshtrivedy
--

COPY public.structures_tags (structure_id, tag_id) FROM stdin;
22f14e5d-02d7-4ff3-a7e4-e31edd05c98a	8b35cb5b-66ae-45cb-ba74-1fb5eca33e9d
2f56c8d3-2cca-45d8-9d93-6dc2e3d90f37	8b35cb5b-66ae-45cb-ba74-1fb5eca33e9d
03ee2406-af44-430d-9347-8b632a4ca67d	8b35cb5b-66ae-45cb-ba74-1fb5eca33e9d
e3962179-9245-494e-93bd-964de08882e5	8b35cb5b-66ae-45cb-ba74-1fb5eca33e9d
5453611c-ae5e-46df-aa8d-bf49d770cbc9	8b35cb5b-66ae-45cb-ba74-1fb5eca33e9d
c44bee2d-b59e-4b70-b98f-af64d25b2811	8b35cb5b-66ae-45cb-ba74-1fb5eca33e9d
\.


--
-- Data for Name: tags; Type: TABLE DATA; Schema: public; Owner: sparshtrivedy
--

COPY public.tags (tag_id, user_sub, name) FROM stdin;
763c29c0-6a5e-48d1-993d-a9f63db240bb	auth0|681d382c228898b5ba13b7be	tag1
92d1dc3c-f6bc-429c-be29-4ae99c64c73d	auth0|681d382c228898b5ba13b7be	tag2
b2531c6f-62ab-4d22-855a-87e4c58a71f7	auth0|681d382c228898b5ba13b7be	tag3
ea3c67c7-0660-4b4d-8bf1-16ffc3fcccdd	auth0|681d382c228898b5ba13b7be	tag4
45f96cf2-4f9a-42f9-8a24-0200eb4e3207	auth0|681d382c228898b5ba13b7be	tag5
9bbc42da-2753-43cd-b593-21bf166020f5	auth0|681d382c228898b5ba13b7be	tag6
37527c5e-edfa-48a5-a274-488ca9325295	auth0|681d382c228898b5ba13b7be	tag7
19dc93e7-9d46-41fe-9c31-767382599f57	auth0|681d382c228898b5ba13b7be	runtime
b5c52135-1c08-47bb-a6bb-f59c98afa5c7	auth0|681d382c228898b5ba13b7be	tag
a3eb6c5d-82d2-45db-89e3-0753fc11d0bf	auth0|681d382c228898b5ba13b7be	new_tag
8b35cb5b-66ae-45cb-ba74-1fb5eca33e9d	auth0|681d382c228898b5ba13b7be	generated
\.


--
-- Data for Name: users; Type: TABLE DATA; Schema: public; Owner: sparshtrivedy
--

COPY public.users (user_sub, email, role, group_id, member_since) FROM stdin;
auth0|686ffc7aa0025875955dae19	member2@test.com	group_admin	2ba29864-e9c7-47b3-a718-3e854857ce57	2025-08-07 23:04:34.955036-07
auth0|687aa1d798113782403234fc	sparsh01@student.ubc.ca	member	\N	2025-08-09 12:20:27.209338-07
auth0|6876a9bc512247093911921c	sparsh01@students.ubc.ca	group_admin	0e141968-a172-4a44-a730-09461d5b84b1	2025-07-24 00:11:09.279346-07
auth0|681d382c228898b5ba13b7be	test@test.com	admin	2ba29864-e9c7-47b3-a718-3e854857ce57	2025-07-24 00:34:33.452788-07
auth0|686f5a35f24ed5b3e3b966ea	testing@testing.com	member	2ba29864-e9c7-47b3-a718-3e854857ce57	2025-07-29 13:52:41.032346-07
auth0|686ffc1ea0025875955dadfe	member@test.com	member	\N	2025-07-31 11:00:04.354136-07
\.


--
-- Name: groups groups_name_key; Type: CONSTRAINT; Schema: public; Owner: sparshtrivedy
--

ALTER TABLE ONLY public.groups
    ADD CONSTRAINT groups_name_key UNIQUE (name);


--
-- Name: groups groups_pkey; Type: CONSTRAINT; Schema: public; Owner: sparshtrivedy
--

ALTER TABLE ONLY public.groups
    ADD CONSTRAINT groups_pkey PRIMARY KEY (group_id);


--
-- Name: jobs jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: sparshtrivedy
--

ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT jobs_pkey PRIMARY KEY (job_id);


--
-- Name: jobs_structures jobs_structures_pkey; Type: CONSTRAINT; Schema: public; Owner: sparshtrivedy
--

ALTER TABLE ONLY public.jobs_structures
    ADD CONSTRAINT jobs_structures_pkey PRIMARY KEY (job_id, structure_id);


--
-- Name: jobs_tags jobs_tags_pkey; Type: CONSTRAINT; Schema: public; Owner: sparshtrivedy
--

ALTER TABLE ONLY public.jobs_tags
    ADD CONSTRAINT jobs_tags_pkey PRIMARY KEY (job_id, tag_id);


--
-- Name: requests requests_pkey; Type: CONSTRAINT; Schema: public; Owner: sparshtrivedy
--

ALTER TABLE ONLY public.requests
    ADD CONSTRAINT requests_pkey PRIMARY KEY (request_id);


--
-- Name: structures structures_pkey; Type: CONSTRAINT; Schema: public; Owner: sparshtrivedy
--

ALTER TABLE ONLY public.structures
    ADD CONSTRAINT structures_pkey PRIMARY KEY (structure_id);


--
-- Name: structures_tags structures_tags_pkey; Type: CONSTRAINT; Schema: public; Owner: sparshtrivedy
--

ALTER TABLE ONLY public.structures_tags
    ADD CONSTRAINT structures_tags_pkey PRIMARY KEY (structure_id, tag_id);


--
-- Name: tags tags_pkey; Type: CONSTRAINT; Schema: public; Owner: sparshtrivedy
--

ALTER TABLE ONLY public.tags
    ADD CONSTRAINT tags_pkey PRIMARY KEY (tag_id);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: sparshtrivedy
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: sparshtrivedy
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (user_sub);


--
-- Name: jobs fk_jobs_user_sub; Type: FK CONSTRAINT; Schema: public; Owner: sparshtrivedy
--

ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT fk_jobs_user_sub FOREIGN KEY (user_sub) REFERENCES public.users(user_sub);


--
-- Name: structures fk_structures_user_sub; Type: FK CONSTRAINT; Schema: public; Owner: sparshtrivedy
--

ALTER TABLE ONLY public.structures
    ADD CONSTRAINT fk_structures_user_sub FOREIGN KEY (user_sub) REFERENCES public.users(user_sub);


--
-- Name: jobs_structures jobs_structures_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: sparshtrivedy
--

ALTER TABLE ONLY public.jobs_structures
    ADD CONSTRAINT jobs_structures_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.jobs(job_id) ON DELETE CASCADE;


--
-- Name: jobs_structures jobs_structures_structure_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: sparshtrivedy
--

ALTER TABLE ONLY public.jobs_structures
    ADD CONSTRAINT jobs_structures_structure_id_fkey FOREIGN KEY (structure_id) REFERENCES public.structures(structure_id) ON DELETE CASCADE;


--
-- Name: jobs_tags jobs_tags_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: sparshtrivedy
--

ALTER TABLE ONLY public.jobs_tags
    ADD CONSTRAINT jobs_tags_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.jobs(job_id) ON DELETE CASCADE;


--
-- Name: jobs_tags jobs_tags_tag_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: sparshtrivedy
--

ALTER TABLE ONLY public.jobs_tags
    ADD CONSTRAINT jobs_tags_tag_id_fkey FOREIGN KEY (tag_id) REFERENCES public.tags(tag_id) ON DELETE CASCADE;


--
-- Name: requests requests_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: sparshtrivedy
--

ALTER TABLE ONLY public.requests
    ADD CONSTRAINT requests_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.groups(group_id);


--
-- Name: requests requests_receiver_sub_fkey; Type: FK CONSTRAINT; Schema: public; Owner: sparshtrivedy
--

ALTER TABLE ONLY public.requests
    ADD CONSTRAINT requests_receiver_sub_fkey FOREIGN KEY (receiver_sub) REFERENCES public.users(user_sub);


--
-- Name: requests requests_sender_sub_fkey; Type: FK CONSTRAINT; Schema: public; Owner: sparshtrivedy
--

ALTER TABLE ONLY public.requests
    ADD CONSTRAINT requests_sender_sub_fkey FOREIGN KEY (sender_sub) REFERENCES public.users(user_sub);


--
-- Name: structures_tags structures_tags_structure_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: sparshtrivedy
--

ALTER TABLE ONLY public.structures_tags
    ADD CONSTRAINT structures_tags_structure_id_fkey FOREIGN KEY (structure_id) REFERENCES public.structures(structure_id) ON DELETE CASCADE;


--
-- Name: structures_tags structures_tags_tag_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: sparshtrivedy
--

ALTER TABLE ONLY public.structures_tags
    ADD CONSTRAINT structures_tags_tag_id_fkey FOREIGN KEY (tag_id) REFERENCES public.tags(tag_id) ON DELETE CASCADE;


--
-- Name: users users_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: sparshtrivedy
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_group_id_fkey FOREIGN KEY (group_id) REFERENCES public.groups(group_id);


--
-- PostgreSQL database dump complete
--

