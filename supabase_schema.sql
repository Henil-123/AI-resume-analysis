-- Create users table
CREATE TABLE IF NOT EXISTS public.users (
    id text PRIMARY KEY,
    name text,
    email text UNIQUE NOT NULL,
    password_hash text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

-- Create candidates table
CREATE TABLE IF NOT EXISTS public.candidates (
    id text PRIMARY KEY,
    name text,
    email text,
    phone text,
    top_skills text[],
    experience_summary text,
    experience_years numeric,
    education text[],
    links text[],
    matched_skills text[],
    missing_skills text[],
    skills_score numeric,
    semantic_score numeric,
    experience_score numeric,
    final_score numeric,
    verdict text,
    explanation text,
    status text DEFAULT 'pending'::text,
    text_hash text UNIQUE,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

-- Set permissions (Allow anon key to do CRUD since backend handles auth internally with JWT)
-- You can configure Row Level Security (RLS) if desired later
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.candidates ENABLE ROW LEVEL SECURITY;

-- Create policies allowing Service Role / Anon to manage all rows (since backend handles logic)
CREATE POLICY "Allow full access to users" ON public.users FOR ALL USING (true);
CREATE POLICY "Allow full access to candidates" ON public.candidates FOR ALL USING (true);
