-- Schéma RAG personnel — Obsidian / sources futures
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE documents (
    id               BIGSERIAL PRIMARY KEY,
    source_id        TEXT UNIQUE NOT NULL,
    nom              TEXT NOT NULL,
    chemin_vault     TEXT NOT NULL,
    mime_type        TEXT NOT NULL DEFAULT 'text/markdown',
    dossier          TEXT NOT NULL DEFAULT 'racine',
    tags             JSONB NOT NULL DEFAULT '[]'::jsonb,
    date_document    DATE,
    modified_time    TIMESTAMPTZ NOT NULL,
    statut_ingestion TEXT NOT NULL,
    ingere_le        TIMESTAMPTZ DEFAULT now(),

    CONSTRAINT chk_statut CHECK (statut_ingestion IN ('complet', 'metadonnees_seul', 'echec'))
);

CREATE INDEX idx_documents_dossier ON documents (dossier);
CREATE INDEX idx_documents_modified ON documents (modified_time);

CREATE TABLE chunks (
    id            BIGSERIAL PRIMARY KEY,
    document_id   BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    type_chunk    TEXT NOT NULL DEFAULT 'contenu',
    contenu       TEXT NOT NULL,
    position      INT,
    embedding     VECTOR(1536),
    dossier       TEXT NOT NULL,
    tags          JSONB NOT NULL DEFAULT '[]'::jsonb,
    date_document DATE,

    CONSTRAINT chk_type_chunk CHECK (type_chunk IN ('contenu', 'fiche_fichier'))
);

CREATE INDEX idx_chunks_dossier ON chunks (dossier);
CREATE INDEX idx_chunks_document ON chunks (document_id);
CREATE INDEX idx_chunks_embedding ON chunks
    USING hnsw (embedding vector_cosine_ops);

CREATE TABLE sync_runs (
    id          BIGSERIAL PRIMARY KEY,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    stats       JSONB,
    success     BOOLEAN DEFAULT true
);

CREATE VIEW couverture_dossiers AS
SELECT
    dossier,
    COUNT(*) AS nb_fichiers,
    COUNT(*) FILTER (WHERE statut_ingestion = 'complet') AS ingeres_complet,
    COUNT(*) FILTER (WHERE statut_ingestion = 'metadonnees_seul') AS metadonnees_seul,
    COUNT(*) FILTER (WHERE statut_ingestion = 'echec') AS echecs
FROM documents
GROUP BY dossier
ORDER BY dossier;
