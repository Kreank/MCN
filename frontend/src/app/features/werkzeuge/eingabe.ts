import { DEZIMAL_UNGUELTIG, deZuApiDezimal } from '../../shared/formular/dezimal';

/**
 * Deutsche Zahleingabe eines Formularfelds als `number` — oder `null`, wenn das
 * Feld leer, unlesbar oder MEHRDEUTIG ist.
 *
 * BEWUSSTE ABWEICHUNG VON DER NOTIZAPP: dort wird jedes Komma stumpf zu einem
 * Punkt (`s.Replace(',', '.')`), „1.500" liest sie damit als **1,5**. In MCN
 * gilt die Regel aus `shared/formular/dezimal.ts`: mehrdeutige Eingaben werden
 * ABGELEHNT statt geraten (der Anwender bekommt eine Fehlermeldung am Feld).
 * Das betrifft ausschliesslich das Einlesen einer mehrdeutigen Eingabe — an der
 * Rechenlogik aendert es nichts.
 */
export function zahlAus(roh: string | null | undefined): number | null {
  const api = deZuApiDezimal(roh);
  if (api === '' || api === DEZIMAL_UNGUELTIG) return null;
  const n = Number(api);
  return Number.isFinite(n) ? n : null;
}
