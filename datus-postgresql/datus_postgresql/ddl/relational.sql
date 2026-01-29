-- Generated from datus_postgresql.storage_ddl
-- Review/approve before executing.
-- Vector dimension: 384 (update if your embedding dim differs)
-- Schema: public

CREATE TABLE IF NOT EXISTS public.subject_nodes (
  namespace TEXT NOT NULL,
  node_id BIGSERIAL NOT NULL,
  parent_id BIGINT,
  name TEXT NOT NULL,
  description TEXT DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (namespace, node_id),
  UNIQUE (namespace, parent_id, name)
);

CREATE TABLE IF NOT EXISTS public.tasks (
  namespace TEXT NOT NULL,
  task_id TEXT NOT NULL,
  task_query TEXT NOT NULL,
  sql_query TEXT,
  sql_result TEXT,
  status TEXT,
  user_feedback TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (namespace, task_id)
);

CREATE TABLE IF NOT EXISTS public.feedback (
  namespace TEXT NOT NULL,
  task_id TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (namespace, task_id)
);

CREATE INDEX IF NOT EXISTS idx_subject_parent_id ON public.subject_nodes(namespace, parent_id);

CREATE INDEX IF NOT EXISTS idx_subject_parent_name ON public.subject_nodes(namespace, parent_id, name);
