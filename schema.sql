-- ============================================================
-- YASH WORLD - Supabase PostgreSQL Schema
-- Run this script in your Supabase SQL Editor:
-- https://supabase.com/dashboard/project/_/sql
-- ============================================================

-- 1. USERS TABLE
CREATE TABLE IF NOT EXISTS public.users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    is_admin BOOLEAN DEFAULT FALSE,
    is_friend BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_login TIMESTAMPTZ
);

-- 2. QUESTIONS TABLE
CREATE TABLE IF NOT EXISTS public.questions (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES public.users(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    image_data TEXT,
    image_filename TEXT,
    video_data TEXT,
    video_filename TEXT,
    audio_data TEXT,
    audio_filename TEXT,
    answer_text TEXT,
    answer_image_data TEXT,
    answer_image_filename TEXT,
    answer_video_data TEXT,
    answer_video_filename TEXT,
    answer_audio_data TEXT,
    answer_audio_filename TEXT,
    has_answer BOOLEAN DEFAULT FALSE,
    is_answered BOOLEAN DEFAULT FALSE,
    display_order INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. REPLIES TABLE
CREATE TABLE IF NOT EXISTS public.replies (
    id SERIAL PRIMARY KEY,
    question_id INT REFERENCES public.questions(id) ON DELETE CASCADE,
    user_id INT REFERENCES public.users(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    image_data TEXT,
    image_filename TEXT,
    video_data TEXT,
    video_filename TEXT,
    audio_data TEXT,
    audio_filename TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. FEEDBACK QUESTIONS TABLE
CREATE TABLE IF NOT EXISTS public.feedback_questions (
    id SERIAL PRIMARY KEY,
    question TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. FEEDBACK RESPONSES TABLE
CREATE TABLE IF NOT EXISTS public.feedback_responses (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES public.users(id) ON DELETE CASCADE,
    question_id INT REFERENCES public.feedback_questions(id) ON DELETE CASCADE,
    rating INT NOT NULL,
    comment TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 6. TYPING TEXTS TABLE
CREATE TABLE IF NOT EXISTS public.typing_texts (
    id SERIAL PRIMARY KEY,
    text TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 7. SITE SETTINGS TABLE
CREATE TABLE IF NOT EXISTS public.site_settings (
    id INT PRIMARY KEY DEFAULT 1,
    site_title TEXT DEFAULT 'YASH WORLD',
    site_tagline TEXT DEFAULT 'Private Messaging Platform',
    welcome_message TEXT DEFAULT '',
    auto_snapshot_enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 8. COUNTERS TABLE
CREATE TABLE IF NOT EXISTS public.counters (
    name VARCHAR(50) PRIMARY KEY,
    last_id INT DEFAULT 0
);

-- 9. FRIEND SNAPSHOTS TABLE
CREATE TABLE IF NOT EXISTS public.friend_snapshots (
    id SERIAL PRIMARY KEY,
    user_id INT REFERENCES public.users(id) ON DELETE CASCADE,
    image_data TEXT NOT NULL,
    image_filename TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- INDEXES FOR MAXIMUM QUERY PERFORMANCE
CREATE INDEX IF NOT EXISTS idx_questions_created_at ON public.questions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_replies_question_id ON public.replies(question_id);
CREATE INDEX IF NOT EXISTS idx_replies_created_at ON public.replies(created_at DESC);

-- STORAGE BUCKET CONFIGURATION (Enable Public Access for Media)
INSERT INTO storage.buckets (id, name, public) 
VALUES ('media', 'media', true) 
ON CONFLICT (id) DO UPDATE SET public = true;

-- STORAGE POLICIES (Allow Public Access for 'media' Bucket)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Public Read Access for Media Bucket') THEN
        CREATE POLICY "Public Read Access for Media Bucket" ON storage.objects FOR SELECT USING (bucket_id = 'media');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Public Insert Access for Media Bucket') THEN
        CREATE POLICY "Public Insert Access for Media Bucket" ON storage.objects FOR INSERT WITH CHECK (bucket_id = 'media');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Public Update Access for Media Bucket') THEN
        CREATE POLICY "Public Update Access for Media Bucket" ON storage.objects FOR UPDATE USING (bucket_id = 'media');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'Public Delete Access for Media Bucket') THEN
        CREATE POLICY "Public Delete Access for Media Bucket" ON storage.objects FOR DELETE USING (bucket_id = 'media');
    END IF;
END $$;
