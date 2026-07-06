-- Migration 0001: Schemas, Erweiterungen, gemeinsame Hilfsfunktionen
-- Grundlage: docs/database/CRM-DATENBANKENTWURF-PHASE-1.md, Abschnitt 7 Schritt 1
-- Voraussetzung: PostgreSQL 13 oder neuer (gcd/lcm für numeric, gen_random_uuid ab PG13 in Core)

BEGIN;

CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE SCHEMA identity;
CREATE SCHEMA property;
CREATE SCHEMA management;
CREATE SCHEMA tenure;
CREATE SCHEMA billing;
CREATE SCHEMA security;
CREATE SCHEMA audit;
-- Technisches Hilfsschema für gemeinsame Funktionen; enthält keine Fachdaten.
CREATE SCHEMA util;

-- Setzt updated_at bei jeder Änderung.
CREATE FUNCTION util.set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

-- Erzwingt Append-only: verbietet UPDATE und DELETE (OPUS-02).
CREATE FUNCTION util.forbid_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Tabelle %.% ist append-only; % ist nicht zulässig',
        TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'raise_exception';
END;
$$;

COMMIT;

-- Rückwärtsstrategie: DROP SCHEMA ... CASCADE in umgekehrter Reihenfolge,
-- nur solange keine Fachdaten entstanden sind. Danach ausschließlich
-- vorwärts gerichtete Korrekturmigrationen.
