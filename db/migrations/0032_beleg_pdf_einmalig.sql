-- Migration 0032: Genau EINE PDF-Ausfertigung je veröffentlichtem Beleg
-- (Review Beleg-PDF, Finding P-1). Der Wettlauf zweier paralleler Erstabrufe
-- konnte zwei Ausfertigungen ablegen — der partielle UNIQUE-Index macht die
-- Einmaligkeit physisch; die API behandelt den Konflikt mit Nachselektion.

BEGIN;

CREATE UNIQUE INDEX uq_file_link_beleg_pdf_rechnung
    ON content.file_link (invoice_id)
    WHERE link_category = 'BELEG_PDF' AND invoice_id IS NOT NULL;

CREATE UNIQUE INDEX uq_file_link_beleg_pdf_angebot
    ON content.file_link (quote_id)
    WHERE link_category = 'BELEG_PDF' AND quote_id IS NOT NULL;

COMMIT;

-- Rückwärtsstrategie: DROP INDEX beider Indizes.
