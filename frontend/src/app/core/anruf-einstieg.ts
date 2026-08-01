/**
 * „Anruf annehmen" als **globaler** Einstieg — der eine Knopf.
 *
 * Vorher lagen vier Neu-Knöpfe nebeneinander, die verschieden viel konnten:
 * „Neuer Auftrag" (Kunde musste existieren) und „Meldung erfassen" (kein
 * Termin) in der Kopfleiste, „Anruf annehmen" und „Neuer Termin" nur in der
 * Plantafel. Ausgerechnet der Weg für den Kunden am Telefon — der einzige, der
 * Kontakt, Auftrag und Termin zusammen anlegt — war der einzige, den man nur
 * auf einer einzigen Seite fand. Wer den Hörer in der Hand hält, soll nicht
 * überlegen müssen, welcher Knopf jetzt der richtige ist, und schon gar nicht
 * erst die Ansicht wechseln.
 *
 * Deshalb: **ein** Knopf, überall an derselben Stelle in der Kopfleiste.
 * Geplantes Anlegen bleibt dort, wo es hingehört — Kontakt in Kontakte,
 * Liegenschaft in Liegenschaften, Auftrag in Aufträge, Termin in der Plantafel.
 * Der Reiter ist der Ort fürs Vorbereiten, dieser Knopf der fürs Sofort.
 *
 * Der Dienst hält nur Zustand; das Formular rendert die App-Schale einmal
 * global (`app.html`). Seiten, die vom Ergebnis betroffen sind, hängen sich an
 * `letztesErgebnis` (die Plantafel lädt daraufhin ihr Board neu).
 */
import { Injectable, computed, signal } from '@angular/core';

import { AnrufResult } from './einsatz.model';

/**
 * Grundraster der Plantafel — dieselbe Nutzereinstellung, die die Steuerleiste
 * des Boards schreibt. Sie steht hier (nicht in `plantafel.ts`), weil sie jetzt
 * zwei Leser hat und `core` die Richtung ist, in die ein Feature zeigen darf.
 */
export const BAND_SPEICHER = 'mcn.plantafel.zeitband';
export const TAG_VON = 7;
export const TAG_BIS = 17;

/** Vorbelegung des Termin-Zeitpunkts im Formular. */
export interface AnrufVorbelegung {
  startDatum: string;
  startZeit: string;
  mitarbeiter: string[];
}

/** Das gemerkte Zeitband, oder das Standardband, wenn nichts Gültiges dasteht. */
function bandLesen(): { von: number; bis: number } {
  try {
    const roh = localStorage.getItem(BAND_SPEICHER);
    if (roh) {
      const w = JSON.parse(roh) as { von?: unknown; bis?: unknown };
      if (typeof w.von === 'number' && typeof w.bis === 'number' && w.von < w.bis) {
        return { von: w.von, bis: w.bis };
      }
    }
  } catch {
    // Kaputter oder gesperrter Speicher: Standardband, keine Fehlermeldung.
  }
  return { von: TAG_VON, bis: TAG_BIS };
}

/** Heute als ISO-Datum (`YYYY-MM-DD`), aus der lokalen Zeit des Bedieners. */
function heuteIso(d: Date): string {
  return [
    d.getFullYear(),
    String(d.getMonth() + 1).padStart(2, '0'),
    String(d.getDate()).padStart(2, '0'),
  ].join('-');
}

@Injectable({ providedIn: 'root' })
export class AnrufEinstieg {
  private readonly _offen = signal(false);
  private readonly _vorbelegung = signal<AnrufVorbelegung>({
    startDatum: '',
    startZeit: '',
    mitarbeiter: [],
  });
  private readonly _ergebnis = signal<AnrufResult | null>(null);
  private meldeUhr: ReturnType<typeof setTimeout> | null = null;

  readonly offen = this._offen.asReadonly();
  readonly vorbelegung = this._vorbelegung.asReadonly();

  /**
   * Das zuletzt Angelegte — Quelle der Bestätigung UND Auslöser für Ansichten,
   * die sich danach neu laden müssen. Jedes Ergebnis ist ein neues Objekt, ein
   * `effect` darauf feuert also genau einmal je Anlage.
   */
  readonly letztesErgebnis = this._ergebnis.asReadonly();

  /**
   * Der Bestätigungstext nennt den Status beim Namen, statt pauschal
   * „freigegeben" zu behaupten: Auf dem Vorlege-Weg steht der Auftrag in
   * FREIGABE_AUSSTEHEND und der Termin ist geplant, aber noch nicht ausführbar —
   * der Monteur darf erst nach der Entscheidung losfahren. Wer das hier nicht
   * erfährt, hält den Vorgang für erledigt und wundert sich am Termintag.
   */
  readonly bestaetigung = computed(() => {
    const res = this._ergebnis();
    if (!res) return null;
    const vorgelegt = res.order_status === 'FREIGABE_AUSSTEHEND';
    const nachsatz = res.im_rueckstand
      ? 'Der Termin liegt im Rückstand.'
      : `Termin ${res.job_number} geplant.`;
    return {
      work_order_id: res.work_order_id,
      order_number: res.order_number,
      // `warten` statt `erfolg` beim Vorlegen: Es ist noch nichts entschieden —
      // dieselbe Unterscheidung, die `.melde--warten` in styles.scss trägt.
      art: vorgelegt ? ('warten' as const) : ('erfolg' as const),
      marke: vorgelegt ? 'Wartet auf Freigabe' : 'Angelegt',
      text: vorgelegt
        ? `Auftrag ${res.order_number} ist erfasst und dem Entscheider vorgelegt. ${nachsatz} Der Monteur darf erst nach der Freigabe losfahren.`
        : `Auftrag ${res.order_number} angelegt und freigegeben. ${nachsatz}`,
    };
  });

  /**
   * Formular öffnen. Ohne Vorbelegung gilt: heute, zur nächsten vollen Stunde,
   * ins gemerkte Zeitband geklemmt — wer nach Feierabend anruft, bekommt einen
   * Termin am nächsten Arbeitsbeginn und nicht um 23:00.
   */
  oeffnen(vorbelegung?: Partial<AnrufVorbelegung>): void {
    const band = bandLesen();
    const jetzt = new Date();
    const naechste = Math.min(Math.max(jetzt.getHours() + 1, band.von), band.bis - 1);

    this._vorbelegung.set({
      startDatum: vorbelegung?.startDatum || heuteIso(jetzt),
      startZeit: vorbelegung?.startZeit || `${`${naechste}`.padStart(2, '0')}:00`,
      mitarbeiter: vorbelegung?.mitarbeiter ?? [],
    });
    this._offen.set(true);
  }

  schliessen(): void {
    this._offen.set(false);
  }

  /** Angelegt: Formular zu, Bestätigung an — sie verschwindet von selbst wieder. */
  fertig(res: AnrufResult): void {
    this._offen.set(false);
    this._ergebnis.set(res);
    if (this.meldeUhr !== null) clearTimeout(this.meldeUhr);
    this.meldeUhr = setTimeout(() => this.bestaetigungSchliessen(), 15000);
  }

  bestaetigungSchliessen(): void {
    if (this.meldeUhr !== null) {
      clearTimeout(this.meldeUhr);
      this.meldeUhr = null;
    }
    this._ergebnis.set(null);
  }
}
