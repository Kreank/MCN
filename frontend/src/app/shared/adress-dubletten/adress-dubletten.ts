import { Component, computed, input, output } from '@angular/core';
import { RouterLink } from '@angular/router';
import {
  Observable,
  catchError,
  debounceTime,
  distinctUntilChanged,
  map,
  of,
  switchMap,
} from 'rxjs';
import {
  AdressDublettenQuery,
  AdressTreffer,
  AdressTrefferArt,
  Property,
} from '../../core/property.model';
import { PropertyService } from '../../core/property.service';
import { RefMerkmal } from '../formular/referenz-wahl';
import { propertyMerkmale } from '../formular/ref-merkmale';

/** Vergleichsschlüssel einer Abfrage — `null` heißt „nicht abfragen". */
function schluessel(q: AdressDublettenQuery | null): string {
  if (!q) return '';
  return [q.street, q.house_number ?? '', q.postal_code ?? '', q.city ?? '']
    .map((s) => s.trim().toLowerCase())
    .join('|');
}

/**
 * Live-Strom der Adressdubletten zu einer Formulareingabe.
 *
 * Der Aufrufer liefert eine Quelle, die bei jeder Formularänderung entweder die
 * Abfrage oder `null` schickt (Vorbedingung nicht erfüllt: keine Straße, keine
 * PLZ/kein Ort, oder es ist bereits eine bestehende Liegenschaft gewählt).
 *
 * Reihenfolge mit Absicht: erst `debounceTime`, DANN `distinctUntilChanged`.
 * Die Quelle hängt an `form.valueChanges` und feuert auch bei Betreff oder
 * Priorität. Weil zuerst entprellt und erst danach verglichen wird, geht nur der
 * Wert im Ruhezustand in den Vergleich — ein Tastendruck in einem FREMDEN Feld
 * ändert den Schlüssel nicht und löst damit keine Anfrage aus. Umgekehrt (erst
 * vergleichen, dann entprellen) käme jeder Zwischenstand der Adressfelder durch
 * den Vergleich, startete die Entprellung neu und könnte am Ende eine Anfrage zu
 * einem Schlüssel abschicken, der schon abgefragt war.
 *
 * Fehler werden geschluckt: Die Warnung ist eine Hilfe, kein Pflichtschritt —
 * ein 403/500 darf die Erfassung nicht stören, nur die Hilfe entfällt dann.
 */
export function adressDublettenStrom(
  svc: PropertyService,
  quelle$: Observable<AdressDublettenQuery | null>,
): Observable<AdressTreffer[]> {
  const leer: AdressTreffer[] = [];
  return quelle$.pipe(
    debounceTime(400),
    distinctUntilChanged((a, b) => schluessel(a) === schluessel(b)),
    switchMap((q) =>
      q
        ? svc.adressDubletten({ limit: 5, ...q }).pipe(
            map((r) => r.treffer ?? leer),
            catchError(() => of(leer)),
          )
        : of(leer),
    ),
  );
}

/**
 * Hinweisfläche „an dieser Adresse gibt es schon etwas".
 *
 * Ausdrücklich ein HINWEIS, kein Blocker: Ohne Auswahl wird ganz normal neu
 * angelegt, und das steht auch so im Panel. Zwei Betriebsarten:
 *
 * - `uebernehmen` — die Erfassung hat ein Zielfeld für die Liegenschaft; der
 *   Treffer kann direkt hineingezogen werden.
 * - `oeffnen` — im Anlagedialog gibt es kein Zielfeld; stattdessen führt ein
 *   Link zur bestehenden Liegenschaft (der Aufrufer schließt den Dialog).
 */
@Component({
  selector: 'app-adress-dubletten',
  imports: [RouterLink],
  templateUrl: './adress-dubletten.html',
  styleUrl: './adress-dubletten.scss',
})
export class AdressDublettenHinweis {
  readonly treffer = input.required<AdressTreffer[]>();
  readonly modus = input<'uebernehmen' | 'oeffnen'>('uebernehmen');

  readonly uebernehmen = output<AdressTreffer>();
  /** Vor der Navigation gefeuert, damit der Aufrufer den Dialog schließen kann. */
  readonly oeffnen = output<AdressTreffer>();

  /**
   * Überschrift: nennt Zahl UND Kern. Bei reinen Straßentreffern heißt es
   * „an dieser Straße" — die Hausnummer weicht dort ab, und „an dieser Adresse"
   * wäre schlicht falsch.
   */
  protected readonly kopfzeile = computed(() => {
    const n = this.treffer().length;
    const wort = n === 1 ? 'Liegenschaft' : 'Liegenschaften';
    const nurStrasse = this.treffer().every((t) => t.art === 'STRASSE');
    return `${n} ${wort} ${nurStrasse ? 'an dieser Straße' : 'an dieser Adresse'}`;
  });

  protected readonly frage = computed(() =>
    this.modus() === 'uebernehmen'
      ? 'Gehört Ihr Anruf zu einer davon?'
      : 'Ist es eine davon?',
  );

  protected readonly fusszeile = computed(() =>
    this.modus() === 'uebernehmen'
      ? 'Keine passt? Dann einfach weiter — es wird eine neue Liegenschaft angelegt.'
      : 'Keine passt? Dann einfach weiter anlegen — der Hinweis blockiert nichts.',
  );

  /**
   * Text der Live-Ansage. Bewusst knapp: Zahl, Kern, Kernfrage und der Hinweis,
   * dass nichts blockiert ist. Leerer String, sobald keine Treffer mehr da sind
   * — das Leeren einer Live-Region löst KEINE Ansage aus, unterdrückt aber ein
   * verspätetes Vorlesen veralteter Treffer.
   */
  protected readonly ansage = computed(() => {
    if (!this.treffer().length) return '';
    return `${this.kopfzeile()} gefunden. ${this.frage()} ${this.fusszeile()}`;
  });

  protected artLabel(a: AdressTrefferArt): string {
    switch (a) {
      case 'EXAKT':
        return 'Gleiche Adresse';
      case 'GEBAEUDE':
        return 'Gebäudeadresse';
      case 'STRASSE':
        return 'Gleiche Straße';
    }
  }

  protected merkmale(p: Property): RefMerkmal[] {
    return propertyMerkmale(p);
  }
}
