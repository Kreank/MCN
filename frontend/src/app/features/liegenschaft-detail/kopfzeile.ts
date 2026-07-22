import { Component, effect, inject, input, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { RouterLink } from '@angular/router';

/** Eine Person in der Kopfzeile — Verwaltung, Eigentümer oder Mieter. */
export interface KopfPerson {
  party_id: string;
  display_name: string;
  rolle: string;
  telefon: string | null;
  /** Nur bei der Verwaltung: was sie darf und bis zu welchem Betrag. */
  befugnis: string | null;
}

export interface Kopfzeile {
  verwaltung: KopfPerson[];
  eigentuemer: KopfPerson[];
  mieter: KopfPerson[];
  /** Wie viele Mieter es INSGESAMT gibt — die Liste ist gedeckelt. */
  mieter_gesamt: number;
  /** Bereiche, für die das Recht fehlt — das UI sagt es, statt zu schweigen. */
  nicht_sichtbar: string[];
}

/**
 * Die Kopfzeile der Liegenschaftsmappe (Arbeitspaket AP2).
 *
 * Sascha: *„Das sind Daten, die der Dispo schnell wissen will."* Verwaltung,
 * Eigentümer und Mieter standen verteilt in drei Reitern; wer am Telefon einen
 * Auftrag entgegennimmt, klickt sie nicht nacheinander zusammen.
 *
 * Die entscheidende Angabe steht an der Verwaltung: **wer bis zu welchem Betrag
 * beauftragen darf** (A-26). Ohne sie nimmt der Disponent einen Auftrag
 * entgegen, den am Ende niemand bezahlen will. „Keine Vollmacht hinterlegt"
 * wird ausdrücklich gesagt — ein leeres Feld ließe offen, ob es keine gibt oder
 * nur niemand eine eingetragen hat.
 *
 * Die Rufnummern stehen als Wähl-Links direkt daran: Der Disponent will
 * anrufen, nicht erst die Kontaktmappe öffnen.
 *
 * **Ein Fehler blendet die Zeile aus, statt die Mappe zu blockieren.** Sie ist
 * eine Zugabe zur Liegenschaft, kein Teil von ihr — wer sie nicht laden kann,
 * soll trotzdem mit der Mappe arbeiten können.
 */
@Component({
  selector: 'app-liegenschaft-kopfzeile',
  imports: [RouterLink],
  templateUrl: './kopfzeile.html',
  styleUrl: './kopfzeile.scss',
})
export class LiegenschaftKopfzeile {
  private readonly http = inject(HttpClient);

  readonly propertyId = input.required<string>();

  /** Eindeutige IDs — zwei Instanzen auf einer Seite brächen sonst die
   *  ARIA-Bezüge (/ zeigten beide auf dieselbe). */
  private static seq = 0;
  protected readonly titelId = `kopfzeile-titel-${++LiegenschaftKopfzeile.seq}`;
  protected readonly inhaltId = `kopfzeile-inhalt-${LiegenschaftKopfzeile.seq}`;

  protected readonly daten = signal<Kopfzeile | null>(null);
  protected readonly offen = signal(true);

  private geladenFuer: string | null = null;
  private reqId = 0;

  constructor() {
    // Dasselbe Muster wie in den Nachbarkomponenten (Belegung, Eigentum): Der
    // Effekt beobachtet die Liegenschaft und lädt bei Wechsel nach.
    effect(() => {
      const id = this.propertyId();
      if (id && this.geladenFuer !== id) {
        this.geladenFuer = id;
        this.laden(id);
      }
    });
  }

  private laden(id: string): void {
    const req = ++this.reqId;
    this.http.get<Kopfzeile>(`/api/property/properties/${id}/kopfzeile`).subscribe({
      next: (d) => {
        if (req === this.reqId) this.daten.set(d);
      },
      // Stillschweigend: Die Kopfzeile ist eine Zugabe. Eine Fehlermeldung über
      // der Mappe wäre lauter als der Nutzen.
      error: () => {
        if (req === this.reqId) this.daten.set(null);
      },
    });
  }

  protected umschalten(): void {
    this.offen.update((v) => !v);
  }

  /** Hat die Zeile überhaupt etwas zu zeigen? */
  protected leer(d: Kopfzeile): boolean {
    return (
      d.verwaltung.length === 0 &&
      d.eigentuemer.length === 0 &&
      d.mieter.length === 0
    );
  }

  /** Rufnummer für `tel:` — Leerzeichen und Trennzeichen stören dort. */
  protected telHref(nummer: string): string {
    return `tel:${nummer.replace(/[\s/()-]/g, '')}`;
  }
}
