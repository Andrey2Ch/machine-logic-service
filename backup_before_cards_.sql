--
-- PostgreSQL database dump
--

-- Dumped from database version 15.12 (Debian 15.12-1.pgdg120+1)
-- Dumped by pg_dump version 17.0

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: postgres
--

-- *not* creating schema, since initdb creates it


ALTER SCHEMA public OWNER TO postgres;

--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: postgres
--

COMMENT ON SCHEMA public IS '';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: access_attempts; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.access_attempts (
    id integer NOT NULL,
    telegram_id bigint NOT NULL,
    username character varying,
    full_name character varying,
    "timestamp" timestamp(6) without time zone NOT NULL,
    processed boolean
);


ALTER TABLE public.access_attempts OWNER TO postgres;

--
-- Name: access_attempts_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.access_attempts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.access_attempts_id_seq OWNER TO postgres;

--
-- Name: access_attempts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.access_attempts_id_seq OWNED BY public.access_attempts.id;


--
-- Name: batch_operations; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.batch_operations (
    id integer NOT NULL,
    batch_id integer,
    new_batch_id integer,
    operation_type character varying(20) NOT NULL,
    quantity integer NOT NULL,
    employee_id integer,
    created_at timestamp(6) without time zone
);


ALTER TABLE public.batch_operations OWNER TO postgres;

--
-- Name: batch_operations_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.batch_operations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.batch_operations_id_seq OWNER TO postgres;

--
-- Name: batch_operations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.batch_operations_id_seq OWNED BY public.batch_operations.id;


--
-- Name: batches; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.batches (
    id integer NOT NULL,
    setup_job_id integer,
    lot_id integer,
    parent_batch_id integer,
    initial_quantity integer NOT NULL,
    current_quantity integer NOT NULL,
    current_location character varying(30) NOT NULL,
    operator_id integer,
    batch_time timestamp(6) without time zone NOT NULL,
    created_at timestamp(6) without time zone,
    recounted_quantity integer,
    warehouse_employee_id integer,
    warehouse_received_at timestamp(6) without time zone,
    qc_inspector_id integer,
    qc_comment text,
    qc_end_time timestamp(3) without time zone,
    qa_date timestamp without time zone,
    discrepancy_percentage real,
    admin_acknowledged_discrepancy boolean DEFAULT false NOT NULL,
    updated_at timestamp without time zone DEFAULT now(),
    operator_reported_quantity integer,
    discrepancy_absolute integer,
    qc_start_time timestamp(6) without time zone
);


ALTER TABLE public.batches OWNER TO postgres;

--
-- Name: batches_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.batches_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.batches_id_seq OWNER TO postgres;

--
-- Name: batches_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.batches_id_seq OWNED BY public.batches.id;


--
-- Name: employees; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.employees (
    id integer NOT NULL,
    telegram_id bigint,
    username character varying,
    full_name character varying,
    role_id integer,
    created_at timestamp(6) without time zone,
    added_by bigint,
    is_active boolean NOT NULL
);


ALTER TABLE public.employees OWNER TO postgres;

--
-- Name: employees_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.employees_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.employees_id_seq OWNER TO postgres;

--
-- Name: employees_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.employees_id_seq OWNED BY public.employees.id;


--
-- Name: lots; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.lots (
    id integer NOT NULL,
    part_id integer,
    lot_number character varying(50) NOT NULL,
    total_planned_quantity integer,
    status character varying(20) NOT NULL,
    created_at timestamp(6) without time zone,
    order_manager_id bigint,
    created_by_order_manager_at timestamp with time zone,
    due_date timestamp with time zone,
    initial_planned_quantity integer
);


ALTER TABLE public.lots OWNER TO postgres;

--
-- Name: COLUMN lots.order_manager_id; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.lots.order_manager_id IS 'ID ?????????? (????????? ???????), ?????????? ? ???? ?????.';


--
-- Name: COLUMN lots.created_by_order_manager_at; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.lots.created_by_order_manager_at IS '????? ???????? ???? ?????????? ???????.';


--
-- Name: COLUMN lots.due_date; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.lots.due_date IS '??????????? ???? ???????? ????.';


--
-- Name: COLUMN lots.initial_planned_quantity; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.lots.initial_planned_quantity IS '?????????????? ???????? ??????????, ????????? ?????????? ???????.';


--
-- Name: lots_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.lots_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.lots_id_seq OWNER TO postgres;

--
-- Name: lots_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.lots_id_seq OWNED BY public.lots.id;


--
-- Name: machine_readings; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.machine_readings (
    id integer NOT NULL,
    employee_id integer,
    machine_id integer,
    reading integer,
    created_at timestamp(6) without time zone
);


ALTER TABLE public.machine_readings OWNER TO postgres;

--
-- Name: machine_readings_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.machine_readings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.machine_readings_id_seq OWNER TO postgres;

--
-- Name: machine_readings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.machine_readings_id_seq OWNED BY public.machine_readings.id;


--
-- Name: machines; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.machines (
    id integer NOT NULL,
    name character varying,
    type character varying(50) NOT NULL,
    created_at timestamp(6) without time zone,
    is_active boolean NOT NULL
);


ALTER TABLE public.machines OWNER TO postgres;

--
-- Name: machines_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.machines_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.machines_id_seq OWNER TO postgres;

--
-- Name: machines_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.machines_id_seq OWNED BY public.machines.id;


--
-- Name: operator_mapping; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.operator_mapping (
    id integer NOT NULL,
    telegram_id bigint NOT NULL,
    username character varying,
    full_name character varying NOT NULL,
    operator_name character varying NOT NULL
);


ALTER TABLE public.operator_mapping OWNER TO postgres;

--
-- Name: operator_mapping_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.operator_mapping_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.operator_mapping_id_seq OWNER TO postgres;

--
-- Name: operator_mapping_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.operator_mapping_id_seq OWNED BY public.operator_mapping.id;


--
-- Name: parts; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.parts (
    id integer NOT NULL,
    drawing_number character varying(50) NOT NULL,
    material text,
    created_at timestamp(6) without time zone
);


ALTER TABLE public.parts OWNER TO postgres;

--
-- Name: COLUMN parts.material; Type: COMMENT; Schema: public; Owner: postgres
--

COMMENT ON COLUMN public.parts.material IS '????????, ?? ???????? ??????????? ??????. ????? ?????????? description.';


--
-- Name: parts_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.parts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.parts_id_seq OWNER TO postgres;

--
-- Name: parts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.parts_id_seq OWNED BY public.parts.id;


--
-- Name: roles; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.roles (
    id integer NOT NULL,
    role_name character varying(50) NOT NULL,
    description text NOT NULL,
    created_at timestamp(6) without time zone
);


ALTER TABLE public.roles OWNER TO postgres;

--
-- Name: roles_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.roles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.roles_id_seq OWNER TO postgres;

--
-- Name: roles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.roles_id_seq OWNED BY public.roles.id;


--
-- Name: setup_defects; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.setup_defects (
    id integer NOT NULL,
    setup_job_id integer,
    defect_quantity integer,
    defect_reason text,
    employee_id integer,
    created_at timestamp(6) without time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.setup_defects OWNER TO postgres;

--
-- Name: setup_defects_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.setup_defects_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.setup_defects_id_seq OWNER TO postgres;

--
-- Name: setup_defects_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.setup_defects_id_seq OWNED BY public.setup_defects.id;


--
-- Name: setup_jobs; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.setup_jobs (
    id integer NOT NULL,
    employee_id integer,
    machine_id integer,
    lot_id integer,
    part_id integer,
    planned_quantity integer NOT NULL,
    status character varying(50),
    start_time timestamp(6) without time zone,
    end_time timestamp(6) without time zone,
    created_at timestamp(6) without time zone,
    cycle_time integer,
    qa_date timestamp(6) with time zone,
    qa_id integer,
    additional_quantity integer DEFAULT 0
);


ALTER TABLE public.setup_jobs OWNER TO postgres;

--
-- Name: setup_jobs_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.setup_jobs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.setup_jobs_id_seq OWNER TO postgres;

--
-- Name: setup_jobs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.setup_jobs_id_seq OWNED BY public.setup_jobs.id;


--
-- Name: setup_quantity_adjustments; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.setup_quantity_adjustments (
    id integer NOT NULL,
    setup_job_id integer,
    created_at timestamp(6) without time zone DEFAULT CURRENT_TIMESTAMP,
    created_by integer,
    auto_adjustment integer DEFAULT 0,
    manual_adjustment integer DEFAULT 0,
    defect_adjustment integer DEFAULT 0,
    total_adjustment integer
);


ALTER TABLE public.setup_quantity_adjustments OWNER TO postgres;

--
-- Name: setup_quantity_adjustments_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.setup_quantity_adjustments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.setup_quantity_adjustments_id_seq OWNER TO postgres;

--
-- Name: setup_quantity_adjustments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.setup_quantity_adjustments_id_seq OWNED BY public.setup_quantity_adjustments.id;


--
-- Name: setup_statuses; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.setup_statuses (
    id integer NOT NULL,
    status_name character varying(50) NOT NULL,
    description character varying,
    created_at timestamp(6) without time zone
);


ALTER TABLE public.setup_statuses OWNER TO postgres;

--
-- Name: setup_statuses_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.setup_statuses_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.setup_statuses_id_seq OWNER TO postgres;

--
-- Name: setup_statuses_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.setup_statuses_id_seq OWNED BY public.setup_statuses.id;


--
-- Name: access_attempts id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.access_attempts ALTER COLUMN id SET DEFAULT nextval('public.access_attempts_id_seq'::regclass);


--
-- Name: batch_operations id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.batch_operations ALTER COLUMN id SET DEFAULT nextval('public.batch_operations_id_seq'::regclass);


--
-- Name: batches id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.batches ALTER COLUMN id SET DEFAULT nextval('public.batches_id_seq'::regclass);


--
-- Name: employees id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.employees ALTER COLUMN id SET DEFAULT nextval('public.employees_id_seq'::regclass);


--
-- Name: lots id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.lots ALTER COLUMN id SET DEFAULT nextval('public.lots_id_seq'::regclass);


--
-- Name: machine_readings id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.machine_readings ALTER COLUMN id SET DEFAULT nextval('public.machine_readings_id_seq'::regclass);


--
-- Name: machines id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.machines ALTER COLUMN id SET DEFAULT nextval('public.machines_id_seq'::regclass);


--
-- Name: operator_mapping id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.operator_mapping ALTER COLUMN id SET DEFAULT nextval('public.operator_mapping_id_seq'::regclass);


--
-- Name: parts id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.parts ALTER COLUMN id SET DEFAULT nextval('public.parts_id_seq'::regclass);


--
-- Name: roles id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.roles ALTER COLUMN id SET DEFAULT nextval('public.roles_id_seq'::regclass);


--
-- Name: setup_defects id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.setup_defects ALTER COLUMN id SET DEFAULT nextval('public.setup_defects_id_seq'::regclass);


--
-- Name: setup_jobs id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.setup_jobs ALTER COLUMN id SET DEFAULT nextval('public.setup_jobs_id_seq'::regclass);


--
-- Name: setup_quantity_adjustments id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.setup_quantity_adjustments ALTER COLUMN id SET DEFAULT nextval('public.setup_quantity_adjustments_id_seq'::regclass);


--
-- Name: setup_statuses id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.setup_statuses ALTER COLUMN id SET DEFAULT nextval('public.setup_statuses_id_seq'::regclass);


--
-- Data for Name: access_attempts; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.access_attempts (id, telegram_id, username, full_name, "timestamp", processed) FROM stdin;
1	453960141	Andrey2Ch	Andrey	2025-04-23 05:27:59.448887	f
\.


--
-- Data for Name: batch_operations; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.batch_operations (id, batch_id, new_batch_id, operation_type, quantity, employee_id, created_at) FROM stdin;
\.


--
-- Data for Name: batches; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.batches (id, setup_job_id, lot_id, parent_batch_id, initial_quantity, current_quantity, current_location, operator_id, batch_time, created_at, recounted_quantity, warehouse_employee_id, warehouse_received_at, qc_inspector_id, qc_comment, qc_end_time, qa_date, discrepancy_percentage, admin_acknowledged_discrepancy, updated_at, operator_reported_quantity, discrepancy_absolute, qc_start_time) FROM stdin;
52	\N	20	50	20	20	archived	2	2025-05-12 11:33:13.437	2025-05-12 11:33:13.437	\N	2	2025-05-11 13:41:29.367	\N	\N	\N	\N	\N	f	2025-05-12 11:33:13.440037	\N	\N	\N
58	\N	20	52	20	20	good	2	2025-05-12 12:13:36.218	2025-05-12 12:13:36.218	\N	2	2025-05-11 13:41:29.367	\N	\N	\N	\N	\N	f	2025-05-12 12:13:36.219857	\N	\N	\N
57	\N	18	55	25	25	archived	2	2025-05-12 11:51:45.827	2025-05-12 11:51:45.827	\N	2	2025-05-08 13:04:12.211	\N	\N	\N	\N	\N	f	2025-05-12 11:51:45.843632	\N	\N	\N
59	\N	18	57	20	20	defect	2	2025-05-12 12:13:48.894	2025-05-12 12:13:48.894	\N	2	2025-05-08 13:04:12.211	\N	\N	\N	\N	\N	f	2025-05-12 12:13:48.897017	\N	\N	\N
60	\N	18	57	5	5	good	2	2025-05-12 12:13:48.894	2025-05-12 12:13:48.894	\N	2	2025-05-08 13:04:12.211	\N	\N	\N	\N	\N	f	2025-05-12 12:13:48.911848	\N	\N	\N
61	20	20	\N	135	22	archived	2	2025-05-12 15:55:03.025895	2025-05-12 15:55:03.025895	22	2	2025-05-12 15:56:30.902464	\N	\N	\N	\N	12	f	2025-05-12 15:57:02.860594	25	-3	\N
63	\N	20	61	7	7	good	2	2025-05-12 12:58:01.866	2025-05-12 12:58:01.866	\N	2	2025-05-12 15:56:30.902	\N	\N	\N	\N	\N	f	2025-05-12 12:58:01.870862	\N	\N	\N
65	\N	20	61	5	5	defect	2	2025-05-12 12:58:01.866	2025-05-12 12:58:01.866	\N	2	2025-05-12 15:56:30.902	\N	\N	\N	\N	\N	f	2025-05-12 12:58:01.894026	\N	\N	\N
2	7	7	\N	0	100	qc_pending	3	2025-04-24 13:28:01.65179	\N	99	422	2025-05-04 09:01:07.554	420	\N	\N	\N	\N	f	2025-05-08 08:52:59.528186	\N	\N	\N
4	12	12	\N	115	200	qc_pending	3	2025-04-29 14:26:36.20562	\N	190	423	2025-04-29 14:37:48.112	422	\N	\N	\N	\N	f	2025-05-08 08:52:59.528186	\N	\N	\N
64	\N	20	61	10	10	archived	2	2025-05-12 12:58:01.866	2025-05-12 12:58:01.866	\N	2	2025-05-12 15:56:30.902	\N	\N	\N	\N	\N	f	2025-05-12 12:58:01.892993	\N	\N	\N
66	\N	20	64	5	5	defect	2	2025-05-12 12:58:24.447	2025-05-12 12:58:24.447	\N	2	2025-05-12 15:56:30.902	\N	\N	\N	\N	\N	f	2025-05-12 12:58:24.449742	\N	\N	\N
5	5	5	\N	35	39	inspection	2	2025-04-29 14:40:33.742528	\N	39	2	2025-05-06 17:50:53.125975	\N	\N	\N	\N	\N	f	2025-05-18 13:19:39.580363	\N	\N	\N
62	18	18	\N	250	245	inspection	2	2025-05-12 15:55:07.326723	2025-05-12 15:55:07.326723	245	2	2025-05-12 15:56:47.147883	\N	\N	\N	\N	2	f	2025-05-18 16:08:23.365575	250	-5	\N
121	\N	13	\N	0	0	sorting	38	2025-05-27 10:23:26.565339	2025-05-27 10:23:26.565339	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-27 10:23:26.567693	\N	\N	\N
124	\N	31	\N	0	0	sorting	2	2025-05-27 12:59:15.767102	2025-05-27 12:59:15.767102	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-27 12:59:15.769104	\N	\N	\N
7	3	3	\N	180	20	archived	425	2025-04-29 15:42:14.796785	\N	18	423	2025-04-29 15:42:30.513	\N	\N	\N	\N	\N	f	2025-05-08 08:52:59.528186	\N	\N	\N
1	3	3	\N	0	100	archived	2	2025-04-23 09:12:52.303737	\N	100	2	2025-05-06 16:55:22.263133	\N	\N	\N	\N	\N	f	2025-05-08 08:52:59.528186	\N	\N	\N
6	12	12	\N	200	50	archived	425	2025-04-29 15:24:15.055477	\N	48	423	2025-04-29 15:24:37.188	422	\N	\N	\N	\N	f	2025-05-08 08:52:59.528186	\N	\N	\N
14	12	12	6	1	1	good	2	2025-05-06 17:22:25.436066	2025-05-06 17:22:25.438069	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-08 08:52:59.528186	\N	\N	\N
8	10	10	\N	100	50	archived	2	2025-05-06 16:49:30.916301	2025-05-06 16:49:30.916301	50	2	2025-05-06 17:24:31.245732	\N	\N	\N	\N	\N	f	2025-05-08 08:52:59.528186	\N	\N	\N
9	10	10	\N	150	50	archived	2	2025-05-06 16:49:35.66529	2025-05-06 16:49:35.66529	50	2	2025-05-06 17:24:35.646085	\N	\N	\N	\N	\N	f	2025-05-08 08:52:59.528186	\N	\N	\N
3	12	12	\N	0	100	inspection	2	2025-04-27 09:30:59.763169	\N	100	2	2025-05-06 17:50:19.836641	\N	\N	\N	\N	\N	f	2025-05-08 08:52:59.528186	\N	\N	\N
17	7	7	\N	300	60	archived	2	2025-05-07 17:02:08.157228	2025-05-07 17:02:08.157228	60	2	2025-05-07 17:02:33.157429	\N	\N	\N	\N	\N	f	2025-05-08 08:52:59.528186	\N	\N	\N
19	7	7	17	20	20	good	2	2025-05-07 17:05:30.521749	2025-05-07 17:05:30.522754	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-08 08:52:59.528186	\N	\N	\N
20	7	7	17	10	10	defect	2	2025-05-07 17:05:30.525072	2025-05-07 17:05:30.525072	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-08 08:52:59.528186	\N	\N	\N
10	7	7	\N	200	95	archived	2	2025-05-06 16:51:26.035518	2025-05-06 16:51:26.035518	95	2	2025-05-06 17:06:13.51877	\N	\N	\N	\N	\N	f	2025-05-08 08:52:59.528186	\N	\N	\N
12	7	7	\N	200	145	archived	2	2025-05-06 13:52:40.264113	\N	145	2	2025-05-06 17:26:22.139673	\N	\N	\N	\N	\N	f	2025-05-08 08:52:59.528186	\N	\N	\N
13	10	10	\N	200	30	archived	2	2025-05-06 16:57:18.573411	2025-05-06 16:57:18.573411	30	2	2025-05-06 17:00:24.194321	\N	\N	\N	\N	\N	f	2025-05-08 08:52:59.528186	\N	\N	\N
15	10	10	\N	100	100	archived	\N	2025-05-06 17:25:14.776819	2025-05-06 17:25:14.777821	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-08 08:52:59.528186	\N	\N	\N
18	13	13	\N	0	25	good	11	2025-05-07 17:02:14.065132	2025-05-07 17:02:14.065132	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-08 08:52:59.528186	\N	\N	\N
23	10	10	\N	130	128	warehouse_counted	2	2025-05-07 17:12:37.898128	2025-05-07 17:12:37.898128	128	2	2025-05-08 08:42:45.483854	\N	\N	\N	\N	\N	f	2025-05-08 08:52:59.528186	\N	\N	\N
24	18	18	\N	0	140	warehouse_counted	2	2025-05-08 09:09:53.42712	2025-05-08 09:09:53.42712	140	2	2025-05-08 11:53:12.135387	\N	\N	\N	\N	6.67	f	2025-05-08 11:53:12.135387	\N	\N	\N
28	14	14	\N	200	40	warehouse_counted	2	2025-05-08 09:10:21.94176	2025-05-08 09:10:21.94176	40	2	2025-05-08 12:43:41.999627	\N	\N	\N	\N	20	f	2025-05-08 12:43:41.999627	50	-10	\N
27	14	14	\N	0	190	warehouse_counted	2	2025-05-08 09:10:11.195797	2025-05-08 09:10:11.195797	190	2	2025-05-08 12:55:44.076181	\N	\N	\N	\N	5	f	2025-05-08 12:55:44.077188	200	-10	\N
31	14	14	\N	250	19	warehouse_counted	2	2025-05-11 08:25:59.385234	2025-05-11 08:25:59.385234	19	2	2025-05-11 08:26:15.990764	\N	\N	\N	\N	5	f	2025-05-11 08:26:15.990764	20	-1	\N
29	18	18	\N	200	48	warehouse_counted	2	2025-05-11 08:25:47.8894	2025-05-11 08:25:47.8894	48	2	2025-05-11 09:23:20.714988	\N	\N	\N	\N	4	f	2025-05-11 09:23:20.714988	50	-2	\N
21	7	7	17	30	30	good	2	2025-05-07 17:05:30.526067	2025-05-07 17:05:30.526067	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-11 10:53:39.393161	\N	\N	\N
26	10	10	\N	230	15	archived	2	2025-05-08 09:10:03.00209	2025-05-08 09:10:03.00209	15	2	2025-05-08 12:59:25.944998	\N	\N	\N	\N	25	f	2025-05-12 12:41:59.466011	20	-5	\N
36	10	10	26	12	12	good	2	2025-05-12 12:41:59.466011	2025-05-12 12:41:59.467708	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-12 12:41:59.467708	\N	\N	\N
37	10	10	26	3	3	defect	2	2025-05-12 12:41:59.471753	2025-05-12 12:41:59.471753	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-12 12:41:59.471753	\N	\N	\N
30	10	10	\N	250	78	warehouse_counted	2	2025-05-11 08:25:55.691843	2025-05-11 08:25:55.691843	78	2	2025-05-12 12:44:30.281027	\N	\N	\N	\N	2.5	f	2025-05-12 12:44:30.281027	80	-2	\N
38	7	7	\N	360	140	archived	2	2025-05-12 12:43:52.946664	2025-05-12 12:43:52.946664	140	2	2025-05-12 12:44:22.664329	\N	\N	\N	\N	0	f	2025-05-12 12:45:19.406848	140	0	\N
39	7	7	38	128	128	good	2	2025-05-12 12:45:19.406848	2025-05-12 12:45:19.407544	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-12 12:45:19.407544	\N	\N	\N
40	7	7	38	12	12	defect	2	2025-05-12 12:45:19.409745	2025-05-12 12:45:19.409745	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-12 12:45:19.409745	\N	\N	\N
11	5	5	\N	60	20	archived	2	2025-05-06 16:51:29.953483	2025-05-06 16:51:29.953483	20	2	2025-05-06 17:06:38.385245	\N	\N	\N	\N	\N	f	2025-05-12 12:46:19.879502	\N	\N	\N
41	5	5	11	20	20	good	2	2025-05-12 12:46:19.879502	2025-05-12 12:46:19.880463	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-12 12:46:19.880463	\N	\N	\N
16	5	5	\N	80	14	archived	2	2025-05-07 17:02:02.671736	2025-05-07 17:02:02.671736	14	2	2025-05-07 17:02:27.218761	\N	\N	\N	\N	\N	f	2025-05-12 12:46:55.45576	\N	\N	\N
42	5	5	16	14	14	rework_repair	2	2025-05-12 12:46:55.45576	2025-05-12 12:46:55.456807	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-12 12:46:55.456807	\N	\N	\N
22	7	7	\N	240	240	archived	\N	2025-05-07 17:07:07.809817	2025-05-07 17:07:07.809817	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-12 13:13:03.150949	\N	\N	\N
43	7	7	22	240	240	rework_repair	2	2025-05-12 13:13:03.149942	2025-05-12 13:13:03.151948	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-12 13:13:03.151948	\N	\N	\N
32	20	20	\N	0	28	archived	2	2025-05-11 12:55:12.439226	2025-05-11 12:55:12.439226	28	2	2025-05-11 12:56:34.960342	\N	\N	\N	\N	6.67	f	2025-05-12 13:29:05.669782	30	-2	\N
44	\N	20	32	28	28	good	2	2025-05-12 11:07:45.866	2025-05-12 11:07:45.866	\N	2	2025-05-11 12:56:34.96	\N	\N	\N	\N	\N	f	2025-05-12 11:07:45.869853	\N	\N	\N
34	20	20	\N	70	35	archived	2	2025-05-11 12:55:25.122675	2025-05-11 12:55:25.122675	35	2	2025-05-11 13:41:22.716633	\N	\N	\N	\N	12.5	f	2025-05-12 12:03:34.453468	40	-5	\N
45	\N	20	34	25	25	good	2	2025-05-12 11:07:55.942	2025-05-12 11:07:55.942	\N	2	2025-05-11 13:41:22.716	\N	\N	\N	\N	\N	f	2025-05-12 11:07:55.94648	\N	\N	\N
46	\N	20	34	10	10	defect	2	2025-05-12 11:07:55.942	2025-05-12 11:07:55.942	\N	2	2025-05-11 13:41:22.716	\N	\N	\N	\N	\N	f	2025-05-12 11:07:55.961722	\N	\N	\N
35	20	20	\N	110	24	archived	2	2025-05-11 13:42:15.040729	2025-05-11 13:42:15.040729	24	2	2025-05-12 11:54:28.770475	\N	\N	\N	\N	4	f	2025-05-12 11:54:46.751781	25	-1	\N
47	\N	20	35	9	9	good	2	2025-05-12 11:08:05.028	2025-05-12 11:08:05.028	\N	2	2025-05-12 11:54:28.77	\N	\N	\N	\N	\N	f	2025-05-12 11:08:05.031166	\N	\N	\N
48	\N	20	35	15	15	archived	2	2025-05-12 11:08:05.028	2025-05-12 11:08:05.028	\N	2	2025-05-12 11:54:28.77	\N	\N	\N	\N	\N	f	2025-05-12 11:08:05.032312	\N	\N	\N
49	\N	20	48	15	15	good	2	2025-05-12 11:21:16.784	2025-05-12 11:21:16.784	\N	2	2025-05-12 11:54:28.77	\N	\N	\N	\N	\N	f	2025-05-12 11:21:16.785047	\N	\N	\N
33	20	20	\N	30	39	archived	2	2025-05-11 12:55:19.357021	2025-05-11 12:55:19.357021	39	2	2025-05-11 13:41:29.367184	\N	\N	\N	\N	2.5	f	2025-05-12 09:58:54.221932	40	-1	\N
51	\N	20	33	9	9	good	2	2025-05-12 11:33:02.895	2025-05-12 11:33:02.895	\N	2	2025-05-11 13:41:29.367	\N	\N	\N	\N	\N	f	2025-05-12 11:33:02.912753	\N	\N	\N
50	\N	20	33	30	30	archived	2	2025-05-12 11:33:02.895	2025-05-12 11:33:02.895	\N	2	2025-05-11 13:41:29.367	\N	\N	\N	\N	\N	f	2025-05-12 11:33:02.898341	\N	\N	\N
53	\N	20	50	10	10	good	2	2025-05-12 11:33:13.437	2025-05-12 11:33:13.437	\N	2	2025-05-11 13:41:29.367	\N	\N	\N	\N	\N	f	2025-05-12 11:33:13.454499	\N	\N	\N
25	18	18	\N	150	45	archived	2	2025-05-08 09:09:58.10681	2025-05-08 09:09:58.10681	45	2	2025-05-08 13:04:12.211311	\N	\N	\N	\N	10	f	2025-05-08 13:10:03.780901	50	-5	\N
54	\N	18	25	15	15	good	2	2025-05-12 11:51:35.62	2025-05-12 11:51:35.62	\N	2	2025-05-08 13:04:12.211	\N	\N	\N	\N	\N	f	2025-05-12 11:51:35.621402	\N	\N	\N
55	\N	18	25	30	30	archived	2	2025-05-12 11:51:35.62	2025-05-12 11:51:35.62	\N	2	2025-05-08 13:04:12.211	\N	\N	\N	\N	\N	f	2025-05-12 11:51:35.637064	\N	\N	\N
56	\N	18	55	5	5	good	2	2025-05-12 11:51:45.827	2025-05-12 11:51:45.827	\N	2	2025-05-08 13:04:12.211	\N	\N	\N	\N	\N	f	2025-05-12 11:51:45.829904	\N	\N	\N
68	9	9	\N	0	146	archived	2	2025-05-15 07:54:58.055436	\N	146	2	2025-05-15 10:55:36.662397	\N	\N	\N	\N	2.67	f	2025-05-15 10:55:53.419398	150	-4	\N
69	\N	9	68	10	10	defect	2	2025-05-15 07:56:10.101	2025-05-15 07:56:10.101	\N	2	2025-05-15 10:55:36.662	\N	\N	\N	\N	\N	f	2025-05-15 07:56:10.107857	\N	\N	\N
70	\N	9	68	136	136	archived	2	2025-05-15 07:56:10.101	2025-05-15 07:56:10.101	\N	2	2025-05-15 10:55:36.662	\N	\N	\N	\N	\N	f	2025-05-15 07:56:10.133215	\N	\N	\N
71	\N	9	70	86	86	good	2	2025-05-15 07:56:19.204	2025-05-15 07:56:19.204	\N	2	2025-05-15 10:55:36.662	\N	\N	\N	\N	\N	f	2025-05-15 07:56:19.206378	\N	\N	\N
72	\N	9	70	50	50	defect	2	2025-05-15 07:56:19.204	2025-05-15 07:56:19.204	\N	2	2025-05-15 10:55:36.662	\N	\N	\N	\N	\N	f	2025-05-15 07:56:19.223215	\N	\N	\N
74	20	20	\N	200	50	warehouse_counted	2	2025-05-18 15:27:43.276623	2025-05-18 15:27:43.276623	50	2	2025-05-18 15:27:53.702606	\N	\N	\N	\N	0	f	2025-05-18 15:27:53.702606	50	0	\N
75	22	22	\N	0	49	warehouse_counted	2	2025-05-19 09:27:16.581895	\N	49	2	2025-05-20 10:14:13.798887	\N	\N	\N	\N	2	f	2025-05-20 10:14:13.798887	50	-1	\N
89	22	22	\N	300	50	production	8	2025-05-21 10:03:28.870939	2025-05-21 10:03:28.870939	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-21 10:03:28.878496	\N	\N	\N
95	\N	22	\N	0	50	archived	2	2025-05-21 11:54:50.545663	2025-05-21 11:54:50.545663	50	2	2025-05-21 11:57:56.518228	\N	\N	\N	\N	\N	f	2025-05-21 11:58:23.547733	0	50	\N
96	\N	22	95	50	50	archived	2	2025-05-21 08:58:42.211	2025-05-21 08:58:42.211	\N	2	2025-05-21 11:57:56.518	\N	\N	\N	\N	\N	f	2025-05-21 08:58:42.213	\N	\N	\N
97	\N	22	96	50	50	good	2	2025-05-21 08:59:03.409	2025-05-21 08:59:03.409	\N	2	2025-05-21 11:57:56.518	\N	\N	\N	\N	\N	f	2025-05-21 08:59:03.411	\N	\N	\N
94	\N	22	\N	0	41	inspection	2	2025-05-21 11:46:08.322659	2025-05-21 11:46:08.322659	41	2	2025-05-21 12:09:36.851866	\N	\N	\N	\N	\N	f	2025-05-21 12:20:27.536886	0	41	\N
99	20	20	\N	400	100	production	11	2025-05-22 13:44:59.786253	2025-05-22 13:44:59.786253	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-22 13:44:59.796808	\N	\N	\N
100	21	21	\N	890	10	production	11	2025-05-22 13:45:03.45551	2025-05-22 13:45:03.45551	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-22 13:45:03.459058	\N	\N	\N
101	18	18	\N	660	20	production	11	2025-05-22 13:45:10.449943	2025-05-22 13:45:10.449943	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-22 13:45:10.453085	\N	\N	\N
102	22	22	\N	500	50	production	11	2025-05-22 14:05:36.613528	2025-05-22 14:05:36.613528	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-22 14:05:36.617041	\N	\N	\N
103	20	20	\N	500	50	production	11	2025-05-22 14:05:43.392507	2025-05-22 14:05:43.392507	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-22 14:05:43.393953	\N	\N	\N
104	20	20	\N	550	10	production	11	2025-05-22 14:05:46.367897	2025-05-22 14:05:46.367897	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-22 14:05:46.370896	\N	\N	\N
76	19	19	\N	1201	9	warehouse_counted	2	2025-05-20 13:55:02.025349	2025-05-20 13:55:02.025349	9	2	2025-05-22 14:26:40.223157	\N	\N	\N	\N	0	f	2025-05-22 14:26:40.223157	9	0	\N
77	22	22	\N	50	99	warehouse_counted	2	2025-05-20 13:55:16.965277	2025-05-20 13:55:16.965277	99	2	2025-05-22 14:26:52.956974	\N	\N	\N	\N	1	f	2025-05-22 14:26:52.956974	100	-1	\N
79	21	21	\N	600	137	warehouse_counted	2	2025-05-20 13:59:42.14951	2025-05-20 13:59:42.14951	137	2	2025-05-22 14:26:59.850833	\N	\N	\N	\N	2.14	f	2025-05-22 14:26:59.850833	140	-3	\N
105	22	22	\N	550	30	production	6	2025-05-22 14:30:02.461175	2025-05-22 14:30:02.461175	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-22 14:30:02.464426	\N	\N	\N
106	\N	22	\N	0	0	sorting	8	2025-05-22 14:31:33.552603	2025-05-22 14:31:33.552603	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-22 14:31:33.552603	\N	\N	\N
107	22	22	\N	580	20	production	8	2025-05-22 14:43:20.437035	2025-05-22 14:43:20.437035	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-22 14:43:20.440204	\N	\N	\N
108	22	22	\N	600	66	production	4	2025-05-22 14:50:22.69084	2025-05-22 14:50:22.69084	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-22 14:50:22.69853	\N	\N	\N
109	20	20	\N	560	20	production	4	2025-05-22 14:59:06.615862	2025-05-22 14:59:06.615862	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-22 14:59:06.62238	\N	\N	\N
67	\N	20	64	5	5	archived	2	2025-05-12 12:58:24.447	2025-05-12 12:58:24.447	\N	2	2025-05-12 15:56:30.902	\N	\N	\N	\N	\N	f	2025-05-12 12:58:24.465318	\N	\N	\N
111	\N	20	67	5	5	good	2	2025-05-22 12:25:53.958	2025-05-22 12:25:53.958	\N	2	2025-05-12 15:56:30.902	\N	\N	\N	\N	\N	f	2025-05-22 12:25:53.959	\N	\N	\N
73	20	20	\N	160	39	archived	2	2025-05-18 13:11:35.905125	2025-05-18 13:11:35.905125	39	2	2025-05-18 15:22:35.375849	\N	\N	\N	\N	2.5	f	2025-05-22 15:25:56.842406	40	-1	\N
112	\N	20	73	29	29	good	2	2025-05-22 12:26:04.827	2025-05-22 12:26:04.827	\N	2	2025-05-18 15:22:35.375	\N	\N	\N	\N	\N	f	2025-05-22 12:26:04.829	\N	\N	\N
113	\N	20	73	10	10	defect	2	2025-05-22 12:26:04.827	2025-05-22 12:26:04.827	\N	2	2025-05-18 15:22:35.375	\N	\N	\N	\N	\N	f	2025-05-22 12:26:04.829	\N	\N	\N
114	22	22	\N	666	34	production	38	2025-05-26 07:40:35.377096	2025-05-26 07:40:35.377096	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-26 07:40:35.390689	\N	\N	\N
115	22	22	\N	700	100	warehouse_counted	2	2025-05-26 12:40:21.343068	2025-05-26 12:40:21.343068	100	2	2025-05-26 17:06:11.674477	\N	\N	\N	\N	0	f	2025-05-26 17:06:11.674477	100	0	\N
78	20	20	\N	250	60	warehouse_counted	2	2025-05-20 13:56:09.781465	2025-05-20 13:56:09.781465	60	2	2025-05-26 17:06:18.386445	\N	\N	\N	\N	0	f	2025-05-26 17:06:18.386445	60	0	\N
80	21	21	\N	740	57	warehouse_counted	2	2025-05-20 14:03:45.038348	2025-05-20 14:03:45.038348	57	2	2025-05-26 17:06:23.435235	\N	\N	\N	\N	5	f	2025-05-26 17:06:23.435235	60	-3	\N
81	18	18	\N	500	158	warehouse_counted	2	2025-05-20 14:14:25.35816	2025-05-20 14:14:25.35816	158	2	2025-05-26 17:06:30.344918	\N	\N	\N	\N	1.25	f	2025-05-26 17:06:30.344918	160	-2	\N
82	20	20	\N	310	350	warehouse_counted	2	2025-05-20 14:28:03.272561	2025-05-20 14:28:03.272561	350	2	2025-05-26 17:06:35.730052	\N	\N	\N	\N	2.78	f	2025-05-26 17:06:35.730052	360	-10	\N
83	22	22	\N	150	155	warehouse_counted	2	2025-05-20 14:28:34.243148	2025-05-20 14:28:34.243148	155	2	2025-05-26 17:06:42.045301	\N	\N	\N	\N	1.9	f	2025-05-26 17:06:42.045301	158	-3	\N
84	20	20	\N	360	380	warehouse_counted	2	2025-05-20 14:35:44.892172	2025-05-20 14:35:44.892172	380	2	2025-05-26 17:06:50.53081	\N	\N	\N	\N	5	f	2025-05-26 17:06:50.53081	400	-20	\N
85	21	21	\N	800	870	warehouse_counted	2	2025-05-20 14:42:42.855965	2025-05-20 14:42:42.855965	870	2	2025-05-26 17:06:57.55053	\N	\N	\N	\N	1.14	f	2025-05-26 17:06:57.55053	880	-10	\N
116	21	21	\N	900	101	production	11	2025-05-26 17:09:23.289539	2025-05-26 17:09:23.289539	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-26 17:09:23.29712	\N	\N	\N
119	14	14	\N	270	30	production	8	2025-05-27 09:52:52.111457	2025-05-27 09:52:52.111457	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-27 09:52:52.11597	\N	\N	\N
120	9	9	\N	150	100	production	8	2025-05-27 09:52:55.021917	2025-05-27 09:52:55.021917	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-27 09:52:55.026428	\N	\N	\N
118	13	13	\N	25	25	warehouse_counted	2	2025-05-27 09:52:36.066598	2025-05-27 09:52:36.066598	25	2	2025-05-27 10:23:14.11655	\N	\N	\N	\N	0	f	2025-05-27 10:23:14.11655	25	0	\N
123	13	13	\N	50	30	production	38	2025-05-27 10:53:26.577708	2025-05-27 10:53:26.577708	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-27 10:53:26.580859	\N	\N	\N
122	27	31	\N	0	150	warehouse_counted	2	2025-05-27 10:52:24.14522	2025-05-27 10:52:24.14522	150	2	2025-05-27 10:53:52.295504	\N	\N	\N	\N	0	f	2025-05-27 10:53:52.295504	150	0	\N
86	21	21	\N	880	850	warehouse_counted	2	2025-05-20 14:43:04.401889	2025-05-20 14:43:04.401889	850	2	2025-05-27 10:54:27.635316	\N	\N	\N	\N	4.49	f	2025-05-27 10:54:27.635316	890	-40	\N
87	22	22	\N	158	260	warehouse_counted	2	2025-05-21 08:36:35.63288	2025-05-21 08:36:35.63288	260	2	2025-05-27 10:54:35.41831	\N	\N	\N	\N	4	f	2025-05-27 10:54:35.41831	250	10	\N
98	22	22	\N	350	100	warehouse_counted	2	2025-05-21 18:26:43.79195	2025-05-21 18:26:43.79195	100	2	2025-05-27 10:54:45.971414	\N	\N	\N	\N	33.33	f	2025-05-27 10:54:45.971414	150	-50	\N
110	20	20	\N	580	500	warehouse_counted	2	2025-05-22 15:25:21.906966	2025-05-22 15:25:21.906966	500	2	2025-05-27 10:54:53.708642	\N	\N	\N	\N	3.85	f	2025-05-27 10:54:53.708642	520	-20	\N
117	30	37	\N	0	800	warehouse_counted	2	2025-05-26 17:10:21.653787	2025-05-26 17:10:21.653787	800	2	2025-05-27 10:55:02.890364	\N	\N	\N	\N	2.44	f	2025-05-27 10:55:02.890364	820	-20	\N
88	22	22	\N	250	310	warehouse_counted	2	2025-05-21 09:15:13.337742	2025-05-21 09:15:13.337742	310	2	2025-05-27 10:55:10.127773	\N	\N	\N	\N	3.33	f	2025-05-27 10:55:10.127773	300	10	\N
125	\N	31	\N	0	0	sorting	2	2025-05-27 13:03:30.628015	2025-05-27 13:03:30.628015	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-27 13:03:30.628015	\N	\N	\N
126	\N	31	\N	0	0	sorting	2	2025-05-27 13:21:29.865521	2025-05-27 13:21:29.865521	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-27 13:21:29.867614	\N	\N	\N
127	\N	31	\N	0	0	sorting	2	2025-05-27 13:27:17.908799	2025-05-27 13:27:17.908799	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-27 13:27:17.908799	\N	\N	\N
128	\N	31	\N	0	0	sorting	2	2025-05-27 13:28:41.465726	2025-05-27 13:28:41.465726	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-27 13:28:41.465726	\N	\N	\N
129	\N	20	\N	0	0	sorting	2	2025-05-27 13:30:13.314563	2025-05-27 13:30:13.314563	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-27 13:30:13.314563	\N	\N	\N
130	\N	14	\N	0	0	sorting	8	2025-05-27 13:32:32.430752	2025-05-27 13:32:32.430752	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-27 13:32:32.431751	\N	\N	\N
131	\N	14	\N	0	0	sorting	8	2025-05-27 13:33:31.242087	2025-05-27 13:33:31.242087	\N	\N	\N	\N	\N	\N	\N	\N	f	2025-05-27 13:33:31.242087	\N	\N	\N
\.


--
-- Data for Name: employees; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.employees (id, telegram_id, username, full_name, role_id, created_at, added_by, is_active) FROM stdin;
2	453960141	Andrey2Ch	Andrey	3	2025-04-23 05:57:30.41329	\N	t
4	5897613446	\N	Misha Z	1	2024-12-04 15:37:16	\N	t
3	1539459172	\N	Roman	1	2024-12-04 19:37:16	\N	t
36	5852289989	\N	Vadim	2	2024-12-26 13:52:20	\N	t
9	1594816064	\N	Aleksey	2	2024-12-04 19:37:16	\N	t
37	1824368848	\N	Yuri	2	2024-12-30 10:24:29	\N	t
38	123456799	\N	Alex	1	2025-02-13 14:38:32	\N	t
417	-19022	nikita	Nikita	2	2025-03-11 16:13:02	\N	f
418	-30502	vladimir	Vladimir	2	2025-03-11 16:13:12	\N	f
10	5046179094	Danila_Mashnakov	DaNiLkA	4	2024-12-04 15:37:16	\N	t
420	1133403061	S_Smolensky	Svetlana	5	2025-03-17 11:15:32	\N	t
13	-1	Luba	Baba Luba	5	2025-03-16 14:27:17	\N	t
422	7634237807	\N	Orly	5	2025-03-18 17:31:50	\N	t
423	1234567891	Ded Mazay	Mazay	6	2025-03-23 15:14:01	\N	t
7	5276858697	TYOM_GH	Artem	4	2024-12-04 15:37:16	\N	t
425	7843180737	\N	Vova	1	2025-04-20 18:49:35	\N	t
5	1838690500	GhDancer	Denis	2	2024-12-04 17:37:16	\N	t
6	611119648	Goauld	Sergey M	1	2024-12-04 19:37:16	\N	t
8	419747940	SergeyZyuzkov	Sergey Z	1	2024-12-04 19:37:16	\N	t
11	5060524560	\N	Misha V	1	2024-12-09 19:25:00	\N	t
421	7617253526	\N	Viktorya	5	2025-03-18 15:13:24	\N	t
426	\N	\N	Yaniv	7	\N	\N	t
427	\N	\N	Yossi	7	\N	\N	t
\.


--
-- Data for Name: lots; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.lots (id, part_id, lot_number, total_planned_quantity, status, created_at, order_manager_id, created_by_order_manager_at, due_date, initial_planned_quantity) FROM stdin;
3	3	2113	250	active	\N	\N	\N	\N	\N
4	3	2114	110	active	\N	\N	\N	\N	\N
5	5	2115	150	active	\N	\N	\N	\N	\N
6	6	2116	180	active	\N	\N	\N	\N	\N
7	7	2117	500	active	\N	\N	\N	\N	\N
8	8	2118	600	active	\N	\N	\N	\N	\N
9	9	2110	500	active	\N	\N	\N	\N	\N
2	2	2112	560	active	\N	\N	\N	\N	\N
12	3	400	1113	active	\N	\N	\N	\N	\N
13	13	2121	500	active	\N	\N	\N	\N	\N
14	14	2123	450	active	\N	\N	\N	\N	\N
15	15	2025	850	active	\N	\N	\N	\N	\N
16	16	2026	800	active	\N	\N	\N	\N	\N
17	17	2027	600	active	\N	\N	\N	\N	\N
18	18	550055	2500	active	\N	\N	\N	\N	\N
19	19	4477	500	active	\N	\N	\N	\N	\N
20	20	888	1000	active	\N	\N	\N	\N	\N
21	21	258	1000	active	\N	\N	\N	\N	\N
22	22	222222	5500	active	\N	\N	\N	\N	\N
23	23	888999	1001	active	\N	\N	\N	\N	\N
24	24	777	550	active	\N	\N	\N	\N	\N
10	10	2111-A	550	active	\N	\N	\N	\N	\N
33	27	878	100	in_production	\N	\N	\N	\N	\N
31	27	888-85	\N	in_production	2025-05-26 09:04:40.707405	2	2025-05-26 06:04:40.706404+00	2025-06-04 21:00:00+00	2000
34	29	889	1000	in_production	2025-05-26 09:41:36.914423	\N	\N	\N	1000
35	24	987-99	\N	new	2025-05-26 13:16:40.801376	2	2025-05-26 10:16:40.799376+00	2025-06-14 21:00:00+00	580
36	18	882255	\N	in_production	2025-05-26 13:17:09.691047	2	2025-05-26 10:17:09.691047+00	2025-06-11 21:00:00+00	200
1	1	2111	100	completed	\N	\N	\N	\N	\N
32	14	88	950	in_production	2025-05-26 09:36:26.207342	2	2025-05-26 06:36:26.207342+00	2025-06-22 21:00:00+00	850
37	18	87744	900	post_production	2025-05-26 10:23:48.20445	\N	\N	\N	800
\.


--
-- Data for Name: machine_readings; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.machine_readings (id, employee_id, machine_id, reading, created_at) FROM stdin;
1	3	2	0	2025-04-23 07:59:06.627741
2	3	2	10	2025-04-23 08:00:18.380038
3	3	1	30	2025-04-23 08:01:00.103419
4	3	2	151	2025-04-23 09:06:07.305604
5	3	3	10	2025-04-23 09:12:52.303737
6	3	1	101	2025-04-23 09:13:14.855831
7	38	5	0	2025-04-23 09:15:20.354434
8	3	3	20	2025-04-23 14:32:45.081164
9	425	3	150	2025-04-23 16:18:20.424166
10	3	2	100	2025-04-24 13:28:01.65179
11	38	2	120	2025-04-27 09:13:00.02367
12	38	3	160	2025-04-27 09:13:18.199418
13	38	2	121	2025-04-27 09:30:29.865502
14	3	1	102	2025-04-27 09:30:59.763169
15	3	4	0	2025-04-27 12:31:17.09932
16	3	4	10	2025-04-27 12:31:20.547414
17	3	4	15	2025-04-27 12:31:27.131087
18	11	4	20	2025-04-27 12:31:56.927242
19	4	18	0	2025-04-27 12:35:57.955494
20	4	18	20	2025-04-27 12:36:07.393553
21	38	1	103	2025-04-29 09:09:02.394628
22	38	1	104	2025-04-29 09:09:05.452416
23	6	3	180	2025-04-29 09:09:44.77344
24	6	2	131	2025-04-29 09:09:48.476646
25	6	4	35	2025-04-29 09:09:51.823586
26	6	1	110	2025-04-29 09:09:58.523614
27	11	1	111	2025-04-29 09:18:17.462561
28	6	1	112	2025-04-29 09:19:50.767039
29	4	1	113	2025-04-29 09:48:31.401991
30	11	1	114	2025-04-29 09:50:25.214581
31	11	1	115	2025-04-29 11:24:38.595887
32	3	1	200	2025-04-29 14:26:36.20562
33	3	4	40	2025-04-29 14:40:33.742528
34	425	1	250	2025-04-29 15:24:15.055477
35	425	3	200	2025-04-29 15:42:14.796785
36	3	1	300	2025-05-05 08:10:59.766958
37	38	1	1200	2025-05-06 11:21:06.940063
38	38	3	260	2025-05-06 11:21:29.250694
39	38	10	0	2025-05-06 11:21:38.194295
40	38	16	0	2025-05-06 11:21:41.701331
41	38	10	851	2025-05-06 11:26:16.516687
42	38	10	852	2025-05-06 11:27:31.601571
43	11	12	0	2025-05-06 11:30:56.362459
44	11	8	0	2025-05-06 11:31:03.541885
45	11	12	600	2025-05-06 11:31:10.518637
46	11	10	853	2025-05-06 11:32:12.422212
47	38	16	601	2025-05-06 11:34:09.78461
48	38	16	602	2025-05-06 11:34:17.978343
49	4	1	1201	2025-05-06 11:34:54.257542
50	4	7	0	2025-05-06 11:35:09.045231
51	4	8	190	2025-05-06 11:35:27.267589
52	4	8	200	2025-05-06 11:35:41.158563
53	38	7	100	2025-05-06 16:31:52.662049
54	38	2	150	2025-05-06 16:32:01.482597
55	38	4	60	2025-05-06 16:32:08.011764
56	38	2	200	2025-05-06 16:32:15.554318
57	6	7	150	2025-05-06 16:49:30.916301
58	6	7	200	2025-05-06 16:49:35.66529
59	4	2	300	2025-05-06 16:51:26.035518
60	4	4	80	2025-05-06 16:51:29.953483
61	38	2	350	2025-05-06 13:52:40.264113
62	425	7	230	2025-05-06 16:57:18.573411
63	3	8	0	2025-05-07 15:58:25.038572
64	8	13	0	2025-05-07 15:58:55.401854
65	8	6	0	2025-05-07 15:58:58.468996
66	8	5	0	2025-05-07 15:59:02.176042
67	11	4	95	2025-05-07 17:02:02.671736
68	11	2	360	2025-05-07 17:02:08.157228
69	11	5	25	2025-05-07 17:02:14.065132
70	11	8	150	2025-05-08 09:09:53.42712
71	6	8	200	2025-05-08 09:09:58.10681
72	6	7	250	2025-05-08 09:10:03.00209
73	425	13	200	2025-05-08 09:10:11.195797
74	8	13	250	2025-05-08 09:10:21.94176
75	38	8	250	2025-05-11 08:25:47.8894
76	6	7	330	2025-05-11 08:25:55.691843
77	6	13	270	2025-05-11 08:25:59.385234
78	425	11	0	2025-05-11 12:55:01.484687
79	425	11	30	2025-05-11 12:55:12.439226
80	8	11	70	2025-05-11 12:55:19.357021
81	6	11	110	2025-05-11 12:55:25.122675
82	3	11	135	2025-05-11 13:42:15.040729
83	4	2	500	2025-05-12 12:43:52.946664
84	11	11	160	2025-05-12 15:55:03.025895
85	11	8	500	2025-05-12 15:55:07.326723
86	11	7	80	2025-05-12 15:55:10.400588
87	425	6	150	2025-05-15 07:54:58.055436
88	8	11	200	2025-05-18 13:11:35.905125
89	4	11	250	2025-05-18 15:27:43.276623
90	4	16	0	2025-05-19 09:26:57.487656
91	4	16	50	2025-05-19 09:27:16.581895
92	38	1	1210	2025-05-20 13:55:02.025349
93	38	16	150	2025-05-20 13:55:16.965277
94	11	11	310	2025-05-20 13:56:09.781465
95	8	12	740	2025-05-20 13:59:42.14951
96	11	12	800	2025-05-20 14:03:45.038348
97	6	8	660	2025-05-20 14:14:25.35816
98	11	11	360	2025-05-20 14:28:03.272561
99	38	16	158	2025-05-20 14:28:34.243148
100	11	11	400	2025-05-20 14:35:44.892172
101	8	12	880	2025-05-20 14:42:42.855965
102	38	12	890	2025-05-20 14:43:04.401889
103	3	16	250	2025-05-21 08:36:35.63288
104	4	16	300	2025-05-21 09:15:13.337742
105	8	16	350	2025-05-21 10:03:28.870939
106	11	16	500	2025-05-21 18:26:43.79195
107	11	11	500	2025-05-22 13:44:59.786253
108	11	12	900	2025-05-22 13:45:03.45551
109	11	8	680	2025-05-22 13:45:10.449943
110	11	16	550	2025-05-22 14:05:36.613528
111	11	11	550	2025-05-22 14:05:43.392507
112	11	11	560	2025-05-22 14:05:46.367897
113	6	16	580	2025-05-22 14:30:02.461175
114	8	16	600	2025-05-22 14:43:20.437035
115	4	16	666	2025-05-22 14:50:22.69084
116	4	11	580	2025-05-22 14:59:06.615862
117	38	11	1100	2025-05-22 15:25:21.906966
118	425	3	0	2025-05-25 07:00:54.599958
119	38	16	700	2025-05-26 07:40:35.377096
120	3	16	800	2025-05-26 12:40:21.343068
121	425	2	0	2025-05-26 10:14:38.791805
122	425	18	0	2025-05-26 10:14:44.246868
123	425	10	0	2025-05-26 10:14:50.46635
124	11	12	1001	2025-05-26 17:09:23.289539
125	3	3	820	2025-05-26 17:10:21.653787
126	8	5	50	2025-05-27 09:52:36.066598
127	8	13	300	2025-05-27 09:52:52.111457
128	8	6	250	2025-05-27 09:52:55.021917
129	6	18	150	2025-05-27 10:52:24.14522
130	38	5	80	2025-05-27 10:53:26.577708
\.


--
-- Data for Name: machines; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.machines (id, name, type, created_at, is_active) FROM stdin;
1	B-38	lathe	2024-12-04 13:37:16	t
2	XD-20	lathe	2024-12-04 13:37:16	t
3	SB-16	lathe	2024-12-04 13:37:16	t
4	XD-38	lathe	2024-12-04 13:37:16	t
5	SR-32	lathe	2024-12-04 13:37:16	t
6	SR-26	lathe	2024-12-04 13:37:16	t
7	SR-22	lathe	2024-12-04 13:37:16	t
8	SR-21	lathe	2024-12-04 13:37:16	t
9	SR-24	lathe	2024-12-04 13:37:16	t
10	D-26	lathe	2024-12-04 13:37:16	t
11	SR-10	lathe	2024-12-04 13:37:16	t
12	SR-20	lathe	2024-12-04 13:37:16	t
13	SR-25	lathe	2024-12-04 13:37:16	t
14	K-16-3	lathe	2024-12-04 13:37:16	t
15	L-20	lathe	2024-12-04 13:37:16	t
16	K-16	lathe	2024-12-04 13:37:16	t
17	SR-23	lathe	2024-12-04 13:37:16	t
18	K-16-2	lathe	2024-12-04 13:37:16	t
\.


--
-- Data for Name: operator_mapping; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.operator_mapping (id, telegram_id, username, full_name, operator_name) FROM stdin;
1	453960141	Andrey2Ch	Andrey	Kavanim
2	6934315426	\N	??????	Sergey V
3	1539459172	\N	Roman Ostrah	Roman
4	5897613446	\N	??????	Misha Z
5	1838690500	GhDancer	Dancergu	Kavanim
6	611119648	Goauld	Mcfox	Sergei M
7	5276858697	TYOM_GH	TYOM	Artem
8	419747940	SergeyZyuzkov	Sergey Zyuzkov	Sergei Z
9	1594816064	\N	??????? ????????????	Roman
10	5046179094	Danila_Mashnakov	DaNiLkA	Roman
\.


--
-- Data for Name: parts; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.parts (id, drawing_number, material, created_at) FROM stdin;
1	1111	\N	\N
5	1115	\N	\N
6	1116	\N	\N
7	1117	\N	\N
8	1118	\N	\N
9	1110	\N	\N
10	1011	\N	\N
2	1112	\N	\N
3	1113	\N	\N
13	1121	\N	\N
14	1123	\N	\N
15	1025	\N	\N
16	1026	\N	\N
17	1027	\N	\N
19	777-89	\N	\N
20	666-66	\N	\N
21	88-88	\N	\N
22	789-987	\N	\N
23	654-888	\N	\N
24	999-99	\N	\N
25	458-888	\N	2025-05-26 07:54:14.448656
26	125-888	\N	2025-05-26 07:58:20.230564
27	55-55-5	\N	2025-05-26 09:04:20.962675
29	777-77	\N	\N
18	777-88	\N	\N
\.


--
-- Data for Name: roles; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.roles (id, role_name, description, created_at) FROM stdin;
3	admin	?????????????	2025-04-23 05:57:21.87291
1	operator	???????? ??????	2024-12-04 13:37:16
2	machinist	????????	2024-12-04 13:37:16
4	blocked	????????????	2024-12-05 16:15:00
5	qc	QC-????????? (???)	2025-03-13 16:18:54
6	warehouse	????????? ??????	2025-03-23 12:25:15
7	viewer	Read-only access to specific reports and dashboards	2025-05-05 05:53:50.299889
8	order_manager	???????? ???????	2025-05-25 13:30:32.989104
\.


--
-- Data for Name: setup_defects; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.setup_defects (id, setup_job_id, defect_quantity, defect_reason, employee_id, created_at) FROM stdin;
\.


--
-- Data for Name: setup_jobs; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.setup_jobs (id, employee_id, machine_id, lot_id, part_id, planned_quantity, status, start_time, end_time, created_at, cycle_time, qa_date, qa_id, additional_quantity) FROM stdin;
2	36	2	2	2	150	completed	\N	2025-04-23 09:06:11.654932	2025-04-23 07:46:09.829309	30	\N	\N	0
1	9	1	1	1	100	completed	\N	2025-04-23 09:13:18.657918	2025-04-23 06:14:46.080565	30	\N	\N	0
18	9	8	18	18	2500	started	2025-05-07 15:58:25.038572	\N	2025-05-07 09:32:23.22512	30	2025-05-07 15:58:07.63103+00	421	0
14	36	13	14	14	450	started	2025-05-07 15:58:55.401854	\N	2025-04-27 09:40:15.535309	30	2025-04-27 12:41:03.193903+00	13	0
9	9	6	9	9	500	started	2025-05-07 15:58:58.468996	\N	2025-04-24 12:35:27.564686	30	2025-04-24 15:37:42.368763+00	420	0
13	9	5	13	13	500	started	2025-05-07 15:59:02.176042	\N	2025-04-27 09:33:35.273808	30	2025-04-27 12:36:27.90446+00	422	0
20	37	11	20	20	1000	started	2025-05-11 12:55:01.484687	\N	2025-05-11 09:52:59.573753	30	2025-05-11 12:53:54.777156+00	420	0
7	9	2	7	7	500	completed	2025-04-24 13:28:01.65179	2025-05-12 12:43:55.2727	2025-04-23 12:02:43.311676	110	2025-04-23 15:08:09.274235+00	420	0
16	37	17	16	16	800	started	\N	\N	2025-04-27 10:45:14.253398	30	\N	422	0
22	5	16	22	22	5500	started	2025-05-19 09:26:57.487656	\N	2025-05-19 09:25:51.055185	30	\N	421	0
4	5	5	4	3	110	completed	2025-04-23 09:15:20.354434	2025-04-27 05:56:25.895745	2025-04-23 09:13:57.192823	30	\N	420	0
19	36	1	19	19	500	completed	\N	2025-05-20 13:55:04.313872	2025-05-07 12:54:47.37601	30	\N	422	0
11	36	18	2	2	560	completed	2025-04-27 12:35:57.952494	2025-04-29 05:24:44.73434	2025-04-24 13:18:47.17169	30	2025-04-24 16:19:57.354659+00	13	0
3	5	3	3	3	250	completed	2025-04-23 09:12:52.303737	2025-05-06 11:21:30.724872	2025-04-23 07:46:33.805302	30	\N	\N	0
26	36	10	33	27	100	queued	\N	\N	2025-05-26 09:21:08.731601	30	\N	\N	0
28	9	14	34	29	1000	created	\N	\N	2025-05-26 09:41:36.903542	30	\N	\N	0
8	36	12	8	8	600	completed	2025-05-06 11:30:56.357914	2025-05-06 11:31:17.995698	2025-04-23 12:58:16.969454	30	2025-04-23 16:03:23.625589+00	421	0
15	9	10	15	15	850	completed	2025-05-06 11:21:38.192294	2025-05-06 11:32:18.141364	2025-04-27 10:44:44.634567	30	\N	422	0
17	36	16	17	17	600	completed	2025-05-06 11:21:41.698331	2025-05-06 11:34:19.444031	2025-04-27 10:45:56.424035	30	2025-04-28 17:50:18.028495+00	420	0
12	36	1	12	3	1113	completed	2025-04-27 09:30:59.763169	2025-05-06 11:34:56.504475	2025-04-24 13:28:40.896639	30	2025-04-24 16:29:34.502807+00	422	0
10	5	7	10	10	550	started	2025-05-06 11:35:09.04323	\N	2025-04-24 12:35:59.576147	30	2025-04-24 15:36:23.753647+00	422	0
6	37	8	6	6	180	completed	2025-05-06 11:31:03.539886	2025-05-06 11:35:42.78809	2025-04-23 11:54:54.930922	30	2025-04-23 14:55:46.663013+00	420	0
27	37	18	31	27	2000	started	2025-05-26 10:14:44.246868	\N	2025-05-26 09:38:26.410878	30	\N	422	0
25	37	10	32	14	850	started	2025-05-26 10:14:50.46635	\N	2025-05-26 09:14:11.593913	30	\N	421	0
24	37	2	24	24	550	completed	2025-05-26 10:14:38.791805	2025-05-26 10:15:11.989053	2025-05-25 07:01:21.127821	35	\N	422	0
23	9	3	23	23	1001	completed	2025-05-25 07:00:54.599958	2025-05-26 10:15:17.324985	2025-05-25 06:45:01.586988	30	\N	13	0
5	9	4	5	5	150	completed	2025-04-27 12:31:17.09332	2025-05-26 10:15:21.078822	2025-04-23 11:38:46.179349	48	2025-04-23 14:47:45.49121+00	420	0
29	9	4	36	18	200	created	\N	\N	2025-05-26 10:23:15.837334	30	\N	\N	0
21	5	12	21	21	1000	completed	\N	2025-05-26 17:09:24.727563	2025-05-19 08:35:36.197569	30	\N	420	0
30	36	3	37	18	800	completed	2025-05-26 17:10:21.653787	2025-05-26 17:10:23.293859	2025-05-26 10:23:48.192415	30	\N	\N	0
\.


--
-- Data for Name: setup_quantity_adjustments; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.setup_quantity_adjustments (id, setup_job_id, created_at, created_by, auto_adjustment, manual_adjustment, defect_adjustment, total_adjustment) FROM stdin;
1	12	2025-04-27 10:29:48.923352	2	0	200	0	\N
2	7	2025-04-27 10:30:35.846291	2	0	100	0	\N
\.


--
-- Data for Name: setup_statuses; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.setup_statuses (id, status_name, description, created_at) FROM stdin;
11	created	??????? ???????	\N
12	started	??????? ????????	\N
13	completed	??????? ?????????	\N
14	stopped	??????? ???????????	\N
15	pending_qc	Waiting for QC approval	\N
16	allowed	Approved by QC	\N
17	idle	Machine is idle	\N
18	queued	Setup is queued	\N
\.


--
-- Name: access_attempts_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.access_attempts_id_seq', 1, true);


--
-- Name: batch_operations_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.batch_operations_id_seq', 1, false);


--
-- Name: batches_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.batches_id_seq', 131, true);


--
-- Name: employees_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.employees_id_seq', 427, true);


--
-- Name: lots_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.lots_id_seq', 37, true);


--
-- Name: machine_readings_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.machine_readings_id_seq', 130, true);


--
-- Name: machines_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.machines_id_seq', 1, false);


--
-- Name: operator_mapping_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.operator_mapping_id_seq', 1, false);


--
-- Name: parts_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.parts_id_seq', 30, true);


--
-- Name: roles_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.roles_id_seq', 7, true);


--
-- Name: setup_defects_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.setup_defects_id_seq', 1, false);


--
-- Name: setup_jobs_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.setup_jobs_id_seq', 30, true);


--
-- Name: setup_quantity_adjustments_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.setup_quantity_adjustments_id_seq', 2, true);


--
-- Name: setup_statuses_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.setup_statuses_id_seq', 1, false);


--
-- Name: access_attempts access_attempts_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.access_attempts
    ADD CONSTRAINT access_attempts_pkey PRIMARY KEY (id);


--
-- Name: batch_operations batch_operations_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.batch_operations
    ADD CONSTRAINT batch_operations_pkey PRIMARY KEY (id);


--
-- Name: batches batches_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.batches
    ADD CONSTRAINT batches_pkey PRIMARY KEY (id);


--
-- Name: employees employees_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.employees
    ADD CONSTRAINT employees_pkey PRIMARY KEY (id);


--
-- Name: lots lots_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.lots
    ADD CONSTRAINT lots_pkey PRIMARY KEY (id);


--
-- Name: machine_readings machine_readings_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.machine_readings
    ADD CONSTRAINT machine_readings_pkey PRIMARY KEY (id);


--
-- Name: machines machines_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.machines
    ADD CONSTRAINT machines_pkey PRIMARY KEY (id);


--
-- Name: operator_mapping operator_mapping_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.operator_mapping
    ADD CONSTRAINT operator_mapping_pkey PRIMARY KEY (id);


--
-- Name: parts parts_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.parts
    ADD CONSTRAINT parts_pkey PRIMARY KEY (id);


--
-- Name: roles roles_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.roles
    ADD CONSTRAINT roles_pkey PRIMARY KEY (id);


--
-- Name: setup_defects setup_defects_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.setup_defects
    ADD CONSTRAINT setup_defects_pkey PRIMARY KEY (id);


--
-- Name: setup_jobs setup_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.setup_jobs
    ADD CONSTRAINT setup_jobs_pkey PRIMARY KEY (id);


--
-- Name: setup_quantity_adjustments setup_quantity_adjustments_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.setup_quantity_adjustments
    ADD CONSTRAINT setup_quantity_adjustments_pkey PRIMARY KEY (id);


--
-- Name: setup_statuses setup_statuses_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.setup_statuses
    ADD CONSTRAINT setup_statuses_pkey PRIMARY KEY (id);


--
-- Name: lots uq_lot_number_global; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.lots
    ADD CONSTRAINT uq_lot_number_global UNIQUE (lot_number);


--
-- Name: employees_telegram_id_key; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX employees_telegram_id_key ON public.employees USING btree (telegram_id);


--
-- Name: idx_batches_current_location; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_batches_current_location ON public.batches USING btree (current_location);


--
-- Name: idx_batches_qc_inspector_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_batches_qc_inspector_id ON public.batches USING btree (qc_inspector_id);


--
-- Name: idx_batches_warehouse_employee_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_batches_warehouse_employee_id ON public.batches USING btree (warehouse_employee_id);


--
-- Name: idx_setup_quantity_adjustments_created_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_setup_quantity_adjustments_created_at ON public.setup_quantity_adjustments USING btree (created_at);


--
-- Name: idx_setup_quantity_adjustments_setup_job_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_setup_quantity_adjustments_setup_job_id ON public.setup_quantity_adjustments USING btree (setup_job_id);


--
-- Name: parts_drawing_number_key; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX parts_drawing_number_key ON public.parts USING btree (drawing_number);


--
-- Name: setup_quantity_adjustments_setup_job_id_key; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX setup_quantity_adjustments_setup_job_id_key ON public.setup_quantity_adjustments USING btree (setup_job_id);


--
-- Name: setup_statuses_status_name_key; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX setup_statuses_status_name_key ON public.setup_statuses USING btree (status_name);


--
-- Name: uq_lot_number_per_part; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX uq_lot_number_per_part ON public.lots USING btree (part_id, lot_number);


--
-- Name: batch_operations batch_operations_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.batch_operations
    ADD CONSTRAINT batch_operations_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES public.batches(id);


--
-- Name: batch_operations batch_operations_employee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.batch_operations
    ADD CONSTRAINT batch_operations_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES public.employees(id);


--
-- Name: batch_operations batch_operations_new_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.batch_operations
    ADD CONSTRAINT batch_operations_new_batch_id_fkey FOREIGN KEY (new_batch_id) REFERENCES public.batches(id);


--
-- Name: batches batches_lot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.batches
    ADD CONSTRAINT batches_lot_id_fkey FOREIGN KEY (lot_id) REFERENCES public.lots(id);


--
-- Name: batches batches_operator_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.batches
    ADD CONSTRAINT batches_operator_id_fkey FOREIGN KEY (operator_id) REFERENCES public.employees(id);


--
-- Name: batches batches_parent_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.batches
    ADD CONSTRAINT batches_parent_batch_id_fkey FOREIGN KEY (parent_batch_id) REFERENCES public.batches(id);


--
-- Name: batches batches_setup_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.batches
    ADD CONSTRAINT batches_setup_job_id_fkey FOREIGN KEY (setup_job_id) REFERENCES public.setup_jobs(id);


--
-- Name: employees employees_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.employees
    ADD CONSTRAINT employees_role_id_fkey FOREIGN KEY (role_id) REFERENCES public.roles(id);


--
-- Name: batches fk_batches_qc_inspector; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.batches
    ADD CONSTRAINT fk_batches_qc_inspector FOREIGN KEY (qc_inspector_id) REFERENCES public.employees(id);


--
-- Name: batches fk_batches_warehouse_employee; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.batches
    ADD CONSTRAINT fk_batches_warehouse_employee FOREIGN KEY (warehouse_employee_id) REFERENCES public.employees(id);


--
-- Name: lots lots_order_manager_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.lots
    ADD CONSTRAINT lots_order_manager_id_fkey FOREIGN KEY (order_manager_id) REFERENCES public.employees(id);


--
-- Name: lots lots_part_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.lots
    ADD CONSTRAINT lots_part_id_fkey FOREIGN KEY (part_id) REFERENCES public.parts(id);


--
-- Name: machine_readings machine_readings_employee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.machine_readings
    ADD CONSTRAINT machine_readings_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES public.employees(id);


--
-- Name: machine_readings machine_readings_machine_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.machine_readings
    ADD CONSTRAINT machine_readings_machine_id_fkey FOREIGN KEY (machine_id) REFERENCES public.machines(id);


--
-- Name: setup_defects setup_defects_employee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.setup_defects
    ADD CONSTRAINT setup_defects_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES public.employees(id);


--
-- Name: setup_defects setup_defects_setup_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.setup_defects
    ADD CONSTRAINT setup_defects_setup_job_id_fkey FOREIGN KEY (setup_job_id) REFERENCES public.setup_jobs(id);


--
-- Name: setup_jobs setup_jobs_employee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.setup_jobs
    ADD CONSTRAINT setup_jobs_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES public.employees(id);


--
-- Name: setup_jobs setup_jobs_lot_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.setup_jobs
    ADD CONSTRAINT setup_jobs_lot_id_fkey FOREIGN KEY (lot_id) REFERENCES public.lots(id);


--
-- Name: setup_jobs setup_jobs_machine_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.setup_jobs
    ADD CONSTRAINT setup_jobs_machine_id_fkey FOREIGN KEY (machine_id) REFERENCES public.machines(id);


--
-- Name: setup_jobs setup_jobs_part_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.setup_jobs
    ADD CONSTRAINT setup_jobs_part_id_fkey FOREIGN KEY (part_id) REFERENCES public.parts(id);


--
-- Name: setup_jobs setup_jobs_qa_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.setup_jobs
    ADD CONSTRAINT setup_jobs_qa_id_fkey FOREIGN KEY (qa_id) REFERENCES public.employees(id);


--
-- Name: setup_jobs setup_jobs_status_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.setup_jobs
    ADD CONSTRAINT setup_jobs_status_fkey FOREIGN KEY (status) REFERENCES public.setup_statuses(status_name);


--
-- Name: setup_quantity_adjustments setup_quantity_adjustments_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.setup_quantity_adjustments
    ADD CONSTRAINT setup_quantity_adjustments_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.employees(id);


--
-- Name: setup_quantity_adjustments setup_quantity_adjustments_setup_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.setup_quantity_adjustments
    ADD CONSTRAINT setup_quantity_adjustments_setup_job_id_fkey FOREIGN KEY (setup_job_id) REFERENCES public.setup_jobs(id);


--
-- Name: SCHEMA public; Type: ACL; Schema: -; Owner: postgres
--

REVOKE USAGE ON SCHEMA public FROM PUBLIC;


--
-- PostgreSQL database dump complete
--

