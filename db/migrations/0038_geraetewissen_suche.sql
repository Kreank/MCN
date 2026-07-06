-- Migration 0038: Volltext-/Ähnlichkeitssuche für das Gerätewissen.
-- pg_trgm ist PostgreSQL-Contrib (offizielle Distribution, wie btree_gist).
-- GIN-Trigramm-Indizes machen ILIKE '%…%' über 285k Artikel + Langtexte schnell.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX idx_article_description_trgm
    ON pricing.article USING gin (description gin_trgm_ops);
CREATE INDEX idx_article_long_description_trgm
    ON pricing.article USING gin (long_description gin_trgm_ops)
    WHERE long_description IS NOT NULL;
CREATE INDEX idx_supplier_ref_number_trgm
    ON pricing.article_supplier_reference USING gin (supplier_article_number gin_trgm_ops);

COMMIT;

-- Rückwärtsstrategie: Indizes droppen (Extension bleibt, sie ist harmlos).
