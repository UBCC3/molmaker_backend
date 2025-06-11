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
-- Name: jobs; Type: TABLE; Schema: public; Owner: -
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
    is_deleted boolean DEFAULT false NOT NULL
);


--
-- Name: jobs_structures; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.jobs_structures (
    job_id uuid NOT NULL,
    structure_id uuid NOT NULL
);


--
-- Name: jobs_tags; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.jobs_tags (
    job_id uuid NOT NULL,
    tag_id uuid NOT NULL
);


--
-- Name: structures; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.structures (
    structure_id uuid NOT NULL,
    user_sub text NOT NULL,
    name text NOT NULL,
    location text NOT NULL,
    notes text,
    uploaded_at timestamp without time zone NOT NULL
);


--
-- Name: structures_tags; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.structures_tags (
    structure_id uuid NOT NULL,
    tag_id uuid NOT NULL
);


--
-- Name: tags; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tags (
    tag_id uuid NOT NULL,
    user_sub text NOT NULL,
    name text NOT NULL
);


--
-- Name: jobs jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.jobs
    ADD CONSTRAINT jobs_pkey PRIMARY KEY (job_id);


--
-- Name: jobs_structures jobs_structures_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.jobs_structures
    ADD CONSTRAINT jobs_structures_pkey PRIMARY KEY (job_id, structure_id);


--
-- Name: jobs_tags jobs_tags_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.jobs_tags
    ADD CONSTRAINT jobs_tags_pkey PRIMARY KEY (job_id, tag_id);


--
-- Name: structures structures_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.structures
    ADD CONSTRAINT structures_pkey PRIMARY KEY (structure_id);


--
-- Name: structures_tags structures_tags_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.structures_tags
    ADD CONSTRAINT structures_tags_pkey PRIMARY KEY (structure_id, tag_id);


--
-- Name: tags tags_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tags
    ADD CONSTRAINT tags_pkey PRIMARY KEY (tag_id);


--
-- Name: jobs_structures jobs_structures_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.jobs_structures
    ADD CONSTRAINT jobs_structures_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.jobs(job_id) ON DELETE CASCADE;


--
-- Name: jobs_structures jobs_structures_structure_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.jobs_structures
    ADD CONSTRAINT jobs_structures_structure_id_fkey FOREIGN KEY (structure_id) REFERENCES public.structures(structure_id) ON DELETE CASCADE;


--
-- Name: jobs_tags jobs_tags_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.jobs_tags
    ADD CONSTRAINT jobs_tags_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.jobs(job_id) ON DELETE CASCADE;


--
-- Name: jobs_tags jobs_tags_tag_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.jobs_tags
    ADD CONSTRAINT jobs_tags_tag_id_fkey FOREIGN KEY (tag_id) REFERENCES public.tags(tag_id) ON DELETE CASCADE;


--
-- Name: structures_tags structures_tags_structure_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.structures_tags
    ADD CONSTRAINT structures_tags_structure_id_fkey FOREIGN KEY (structure_id) REFERENCES public.structures(structure_id) ON DELETE CASCADE;


--
-- Name: structures_tags structures_tags_tag_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.structures_tags
    ADD CONSTRAINT structures_tags_tag_id_fkey FOREIGN KEY (tag_id) REFERENCES public.tags(tag_id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

