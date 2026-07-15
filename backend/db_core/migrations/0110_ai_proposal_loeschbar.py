"""ai.ai_proposal: REJECTED/EXPIRED werden löschbar (DSGVO Art. 17).

Hand-SQL nach db/README.md. Entscheidung M3 (aus der Stufe-5-Review): Der von der KI
entworfene Bericht landet im unveränderlichen `proposed_payload` und leitet sich aus
dem (personenbezogenen) Transkript ab. Ein **abgelehnter oder abgelaufener** Vorschlag
hat keine Aufbewahrungsgrundlage — er muss dem Löschanspruch weichen. Ein **genehmigter**
Vorschlag wird zur Grundlage eines materialisierten Belegs (GoBD-Aufbewahrung), ein
**PENDING** wartet noch auf die Entscheidung — beide bleiben unlöschbar.

Deshalb: der pauschale No-Delete-Trigger von 0027 wird durch einen bedingten ersetzt,
der nur REJECTED/EXPIRED zum Löschen durchlässt. No-Truncate bleibt unangetastet; die
Idempotenz-/Unveränderlichkeits-Trigger (guard_ai_proposal) ebenso.
"""
from django.db import migrations

FORWARD_SQL = r"""
DROP TRIGGER trg_ai_proposal_no_delete ON ai.ai_proposal;

CREATE FUNCTION ai.guard_ai_proposal_delete() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status NOT IN ('REJECTED', 'EXPIRED') THEN
        RAISE EXCEPTION
            'ai_proposal %: nur REJECTED/EXPIRED sind loeschbar (Status ist %)',
            OLD.id, OLD.status;
    END IF;
    RETURN OLD;
END;
$$;

CREATE TRIGGER trg_ai_proposal_delete_guard
    BEFORE DELETE ON ai.ai_proposal
    FOR EACH ROW EXECUTE FUNCTION ai.guard_ai_proposal_delete();
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS trg_ai_proposal_delete_guard ON ai.ai_proposal;
DROP FUNCTION IF EXISTS ai.guard_ai_proposal_delete() CASCADE;
CREATE TRIGGER trg_ai_proposal_no_delete
    BEFORE DELETE ON ai.ai_proposal
    FOR EACH ROW EXECUTE FUNCTION util.forbid_mutation();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("db_core", "0109_hero_notiz_label_felder"),
    ]

    operations = [
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
