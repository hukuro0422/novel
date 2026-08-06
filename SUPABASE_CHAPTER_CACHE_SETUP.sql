-- Novel Downloader v2: 本文キャッシュ
-- Supabase SQL Editorで一度だけ実行してください。

create table if not exists public.novel_chapters (
  id bigint generated always as identity primary key,
  novel_id bigint not null references public.novels(id) on delete cascade,
  email varchar(255) not null,
  episode_id text not null,
  episode_index integer not null check (episode_index > 0),
  chapter_title text not null default '',
  title text not null,
  body_html text not null,
  source_url text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (novel_id, episode_id)
);

create index if not exists novel_chapters_novel_order_idx
  on public.novel_chapters (novel_id, episode_index);

create index if not exists novel_chapters_email_novel_idx
  on public.novel_chapters (email, novel_id);

alter table public.novel_chapters enable row level security;

-- 本文は公開クライアントから直接触らせず、Streamlitサーバーだけが扱います。
revoke all on table public.novel_chapters from anon, authenticated;
grant select, insert, update, delete on table public.novel_chapters to service_role;
grant usage, select on sequence public.novel_chapters_id_seq to service_role;

comment on table public.novel_chapters is
  '取得済み本文を保存し、新着話だけの取得で完全版EPUBを再生成するためのキャッシュ';
