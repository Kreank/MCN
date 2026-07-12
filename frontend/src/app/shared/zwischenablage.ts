/**
 * Text in die Zwischenablage legen. `navigator.clipboard` gibt es nur in
 * sicheren Kontexten (https bzw. localhost); scheitert es, faellt die Funktion
 * auf das alte `execCommand('copy')` zurueck. Der Aufrufer erfaehrt ueber das
 * Promise, ob es geklappt hat — er darf NIE „kopiert" melden, ohne das zu
 * pruefen (die NotizApp macht es genauso: sie faengt den Fehlerfall ab).
 */
export async function inZwischenablage(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Faellt auf den Ersatzweg zurueck (z. B. verweigerte Berechtigung).
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    // Ausserhalb des Sichtbereichs, aber fokussierbar — sonst kopiert Safari nicht.
    ta.style.position = 'fixed';
    ta.style.top = '-1000px';
    ta.style.opacity = '0';
    // WICHTIG: Ist ein natives modales <dialog> offen, ist alles ausserhalb davon
    // `inert` — ein an <body> gehaengtes Textarea liesse sich weder fokussieren
    // noch selektieren, und der Ersatzweg schlueg im Dialog IMMER fehl. Deshalb
    // im offenen Dialog dort einhaengen.
    const wurzel: HTMLElement = document.querySelector('dialog[open]') ?? document.body;
    wurzel.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    wurzel.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}
