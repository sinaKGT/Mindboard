-- Mindboard schema v1

CREATE TABLE board (
  id             TEXT PRIMARY KEY,
  title          TEXT NOT NULL,
  description    TEXT NOT NULL DEFAULT '',
  schema_version INTEGER NOT NULL,
  revision       INTEGER NOT NULL DEFAULT 0,
  settings_json  TEXT NOT NULL DEFAULT '{}',
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

CREATE TABLE node (
  id           TEXT PRIMARY KEY,
  board_id     TEXT NOT NULL REFERENCES board(id) ON DELETE CASCADE,
  parent_id    TEXT REFERENCES node(id) ON DELETE CASCADE,
  type         TEXT NOT NULL,
  x            REAL NOT NULL DEFAULT 0,
  y            REAL NOT NULL DEFAULT 0,
  w            REAL,
  h            REAL,
  z            INTEGER NOT NULL DEFAULT 0,
  title        TEXT NOT NULL DEFAULT '',
  content_json TEXT NOT NULL DEFAULT '{}',
  style_json   TEXT NOT NULL DEFAULT '{}',
  collapsed    INTEGER NOT NULL DEFAULT 1,
  provenance   TEXT NOT NULL DEFAULT 'user',
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);
CREATE INDEX node_board_parent ON node(board_id, parent_id);

CREATE TABLE edge (
  id            TEXT PRIMARY KEY,
  board_id      TEXT NOT NULL REFERENCES board(id) ON DELETE CASCADE,
  source_id     TEXT NOT NULL REFERENCES node(id) ON DELETE CASCADE,
  target_id     TEXT NOT NULL REFERENCES node(id) ON DELETE CASCADE,
  source_handle TEXT,
  target_handle TEXT,
  label         TEXT NOT NULL DEFAULT '',
  kind          TEXT NOT NULL DEFAULT 'relates',
  directed      INTEGER NOT NULL DEFAULT 1,
  style_json    TEXT NOT NULL DEFAULT '{}',
  provenance    TEXT NOT NULL DEFAULT 'user',
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE INDEX edge_board_source ON edge(board_id, source_id);
CREATE INDEX edge_board_target ON edge(board_id, target_id);

CREATE TABLE asset (
  sha256      TEXT PRIMARY KEY,
  mime        TEXT NOT NULL,
  ext         TEXT NOT NULL DEFAULT '',
  bytes       INTEGER NOT NULL,
  orig_name   TEXT,
  width       INTEGER,
  height      INTEGER,
  duration_ms INTEGER,
  source_url  TEXT,
  created_at  TEXT NOT NULL
);

CREATE TABLE node_asset (
  node_id TEXT NOT NULL REFERENCES node(id) ON DELETE CASCADE,
  sha256  TEXT NOT NULL REFERENCES asset(sha256),
  role    TEXT NOT NULL DEFAULT 'primary',
  PRIMARY KEY (node_id, sha256, role)
);

-- Reserved for v2 semantic search. Unused in v1 on purpose (see DESIGN.md 11).
CREATE TABLE node_embedding (
  node_id TEXT PRIMARY KEY REFERENCES node(id) ON DELETE CASCADE,
  model   TEXT NOT NULL,
  vec     BLOB NOT NULL
);

CREATE VIRTUAL TABLE node_fts USING fts5(node_id UNINDEXED, title, body);
