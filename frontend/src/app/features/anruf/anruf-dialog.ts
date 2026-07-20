/**
 * Anruf annehmen — ein Formular für den häufigsten Vorgang im Betrieb.
 *
 * Der Ablauf, den dieses Formular abbildet, ist der aus dem Betriebsalltag:
 * Kunde ruft an → gibt es ihn schon? → wo ist es? → was ist kaputt? → wann
 * kommen wir? → fertig. Fachlich entstehen dabei drei Entitäten (Kontakt,
 * Auftrag, Einsatz); im Kopf des Disponenten ist es **eine** Sache. Deshalb
 * nennt diese Maske weder „Auftrag" noch „Einsatz" — sie fragt, was am Telefon
 * gefragt wird, und der Server klammert den Rest in eine Transaktion.
 *
 * Der Vorgang (Eingangskorb) entsteht bewusst NICHT: Er ist die Vorstufe für
 * Meldungen ohne Termin. Wer am Telefon schon terminiert, braucht ihn nicht.
 *
 * Aufbau und Umschaltlogik folgen `features/schnellerfassung` — dort ist das
 * Muster „bestehenden wählen ODER neu anlegen" samt Dublettenwarnung schon
 * gelöst. Abweichung: Hier ist es ein Dialog (die Plantafel bleibt sichtbar
 * dahinter), und am Ende steht ein Termin statt einer Meldung.
 *
 * Zwei Ausgänge, ein Formular: Der Normalfall gibt den Auftrag frei — das
 * Telefonat ist der Beauftragungsnachweis. Übersteigt die Beauftragung aber die
 * Kompetenz der Disposition („will der Chef die Komplettsanierung überhaupt
 * annehmen?"), bliebe sonst nur Freigeben (entscheidet etwas, das ihr nicht
 * zusteht) oder Auflegen (der Anruf verpufft). Der zweite Ausgang legt den
 * Auftrag deshalb VOR: erfasst, terminiert, aber bewusst nicht entschieden.
 */
import { HttpErrorResponse } from '@angular/common/http';
import { Component, computed, effect, inject, input, output, signal, viewChild } from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { map, startWith } from 'rxjs';

import { AnrufIn, AnrufResult } from '../../core/einsatz.model';
import { EinsatzService } from '../../core/einsatz.service';
import { FirmaService } from '../../core/firma.service';
import { PartyService } from '../../core/party.service';
import { PropertyService } from '../../core/property.service';
import {
  AdressDublettenQuery,
  AdressTreffer,
  PropertyType,
} from '../../core/property.model';
import {
  AdressDublettenHinweis,
  adressDublettenStrom,
} from '../../shared/adress-dubletten/adress-dubletten';
import { vonLokalerEingabe } from '../../shared/datum';
import { Dialog } from '../../shared/dialog/dialog';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { felderAlsBeruehrtMarkieren, serverFehlerZuruecksetzen } from '../../shared/formular/formular.util';
import { propertyRefOption, partyRefOption } from '../../shared/formular/ref-merkmale';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';

/** Objekttypen zur Auswahl. EINFAMILIENHAUS steht vorn: Es ist der häufigste
 * Anruf-Fall und der einzige, bei dem der Verantwortungsbereich eindeutig ist. */
const OBJEKTTYPEN: FeldOption[] = [
  { wert: 'EINFAMILIENHAUS', label: 'Einfamilienhaus' },
  { wert: 'WEG', label: 'Eigentümergemeinschaft (WEG)' },
  { wert: 'RENTAL_PROPERTY', label: 'Mietobjekt' },
  { wert: 'COMMERCIAL', label: 'Gewerbe' },
  { wert: 'MIXED', label: 'Gemischt' },
  { wert: 'OTHER', label: 'Sonstiges' },
];

/** Verantwortungsbereich — nur abgefragt, wenn er sich nicht ableiten lässt.
 * Beim Einfamilienhaus gibt es kein Gemeinschaftseigentum. */
const BEREICHE: FeldOption[] = [
  { wert: 'PRIVATE_UNIT', label: 'Sondereigentum (Wohnung/Einheit)' },
  { wert: 'COMMON_PROPERTY', label: 'Gemeinschaftseigentum' },
  { wert: 'MIXED', label: 'Gemischt' },
];

/** Kalendertag danach, als ISO-Datum (`YYYY-MM-DD`). Über `Date` gerechnet,
 * damit Monats- und Jahreswechsel stimmen; Mittag als Uhrzeit vermeidet, dass
 * eine Zeitumstellung den Tag kippt. */
function naechsterTag(iso: string): string {
  const d = new Date(`${iso}T12:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  d.setDate(d.getDate() + 1);
  return [
    d.getFullYear(),
    String(d.getMonth() + 1).padStart(2, '0'),
    String(d.getDate()).padStart(2, '0'),
  ].join('-');
}

const PRIORITAETEN: FeldOption[] = [
  { wert: 'NIEDRIG', label: 'Niedrig' },
  { wert: 'NORMAL', label: 'Normal' },
  { wert: 'DRINGEND', label: 'Dringend' },
];

@Component({
  selector: 'app-anruf-dialog',
  imports: [ReactiveFormsModule, Dialog, Feld, ReferenzWahl, AdressDublettenHinweis],
  templateUrl: './anruf-dialog.html',
  styleUrl: './anruf-dialog.scss',
})
export class AnrufDialog {
  private readonly fb = inject(FormBuilder);
  private readonly svc = inject(EinsatzService);
  private readonly propertySvc = inject(PropertyService);
  private readonly partySvc = inject(PartyService);
  private readonly firmaSvc = inject(FirmaService);

  readonly offen = input(false);
  /** Vorbelegung aus der Plantafel: angeklickter Slot bzw. Bahn. */
  readonly startDatum = input<string>('');
  readonly startZeit = input<string>('');
  readonly mitarbeiterVorauswahl = input<string[]>([]);

  readonly angelegt = output<AnrufResult>();
  readonly abbrechen = output<void>();

  protected readonly laedt = signal(false);
  protected readonly formularMeldung = signal<string | null>(null);

  /**
   * Welcher Ausgang gewählt ist: `false` = freigeben (Normalfall), `true` =
   * dem Entscheider vorlegen. Ein Signal statt eines Formularfelds, weil es
   * nicht der Auftrag ist, der eine Eigenschaft bekommt, sondern der Abschluss,
   * der einen Weg nimmt — und weil davon Sichtbarkeit UND Pflichtfelder
   * abhängen, nicht nur der Payload.
   */
  protected readonly vorlegen = signal(false);

  /** Gewählter BESTEHENDER Kontakt — leer = neuen anlegen. */
  protected readonly bestehenderKontaktId = signal<string>('');
  /** Gewählte BESTEHENDE Liegenschaft — leer = neue anlegen. */
  protected readonly bestehendeObjektId = signal<string>('');

  protected readonly dubletten = signal<AdressTreffer[]>([]);
  protected readonly gewerkOptionen = signal<FeldOption[]>([]);
  protected readonly mitarbeiter = signal<{ id: string; name: string }[]>([]);
  protected readonly gewaehlteMitarbeiter = signal<string[]>([]);

  protected readonly objekttypen = OBJEKTTYPEN;
  protected readonly bereiche = BEREICHE;
  protected readonly prioritaeten = PRIORITAETEN;

  // `read` ist Pflicht: Eine Suche nach dem Typ fände die ERSTE `ReferenzWahl`
  // im Template — das ist der Kontakt-Picker, nicht der für die Liegenschaft.
  private readonly objektWahl = viewChild('objektWahl', { read: ReferenzWahl });

  protected readonly kontaktSuche: RefSuche = (q) =>
    this.partySvc.list({ page: 1, page_size: 20, q }).pipe(map((p) => p.items.map(partyRefOption)));

  protected readonly objektSuche: RefSuche = (q) =>
    this.propertySvc
      .list({ page: 1, page_size: 20, q })
      .pipe(map((p) => p.items.map(propertyRefOption)));

  protected readonly form = this.fb.group({
    existing_party_id: this.fb.control('', { nonNullable: true }),
    salutation: this.fb.control('', { nonNullable: true }),
    first_name: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(200)],
    }),
    last_name: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(200)],
    }),
    phone: this.fb.control('', { nonNullable: true }),
    email: this.fb.control('', { nonNullable: true, validators: [Validators.email] }),

    existing_property_id: this.fb.control('', { nonNullable: true }),
    property_type: this.fb.control<PropertyType>('EINFAMILIENHAUS', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    street: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    house_number: this.fb.control('', { nonNullable: true }),
    postal_code: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    city: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),

    title: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.maxLength(300)],
    }),
    description: this.fb.control('', { nonNullable: true }),
    trade_id: this.fb.control('', { nonNullable: true }),
    priority: this.fb.control('NORMAL', { nonNullable: true }),
    is_emergency: this.fb.control(false, { nonNullable: true }),
    responsibility_scope: this.fb.control('', { nonNullable: true }),
    // Pflicht nur im Vorlege-Weg — siehe `pflichtfelderSynchronisieren()`.
    vorlage_frage: this.fb.control('', { nonNullable: true }),

    start_datum: this.fb.control('', { nonNullable: true }),
    start_zeit: this.fb.control('', { nonNullable: true }),
    end_zeit: this.fb.control('', { nonNullable: true }),
    access_instructions: this.fb.control('', { nonNullable: true }),
  });

  /**
   * Die Formularwerte als Signal.
   *
   * Ohne diesen Umweg wären die `computed()` unten dauerhaft eingefroren: Ein
   * `FormControl` ist kein Signal, ein computed, das nur Controls liest, hat
   * KEINE Producer — und ein computed ohne Producer wird nie wieder als stale
   * markiert. Es behielte den Wert seiner ersten Auswertung für die Lebensdauer
   * der Komponente. Da die Plantafel den Dialog ungebunden rendert (nur das
   * native `<dialog>` blendet ihn aus), fiele diese erste Auswertung auf das
   * leere Formular — `imRueckstand()` wäre für immer `true` und jeder Termin
   * landete im Rückstand, egal was der Disponent einträgt.
   */
  private readonly werte = toSignal(this.form.valueChanges, {
    initialValue: this.form.getRawValue(),
  });

  /**
   * Der Verantwortungsbereich wird nur gefragt, wenn er sich nicht ableiten
   * lässt — beim Einfamilienhaus gibt es kein Gemeinschaftseigentum. Bei einer
   * BESTEHENDEN Liegenschaft kennt das Formular den Typ nicht (der Server leitet
   * dort bewusst nichts ab), also muss gefragt werden.
   *
   * Im Notfall entfällt die Frage ganz: Die DB lässt die Freigabe dann ohne
   * bestätigte Verantwortung zu (A-23, Gefahrenabwehr) — beim Wasserrohrbruch
   * wird nicht erst geklärt, wem das Rohr gehört.
   *
   * Beim Vorlegen entfällt sie ebenso: Der Auftrag endet in FREIGABE_AUSSTEHEND,
   * und die Tore prüfen erst ab FREIGEGEBEN. Wer die Beauftragung fachlich nicht
   * beurteilen kann, kann die Zuordnung Sonder-/Gemeinschaftseigentum meist auch
   * nicht treffen — hier zu raten hieße, einen falschen Kostenträger in die
   * Rechnung zu schreiben. Der Entscheider ergänzt beides in einem Zug.
   */
  protected readonly bereichNoetig = computed(() => {
    // `bestehendeObjektId()` zuerst lesen: Bei einem frühen `return` darunter
    // bliebe es sonst ungelesen und das computed hinge nur noch an `werte` —
    // korrekt wäre es weiterhin, aber die Abhängigkeit soll nicht davon
    // abhängen, welchen Zweig der Wert gerade nimmt.
    const bestehend = !!this.bestehendeObjektId();
    const v = this.werte();
    if (this.vorlegen()) return false;
    if (v.is_emergency) return false;
    if (bestehend) return true;
    return v.property_type !== 'EINFAMILIENHAUS';
  });

  /** Ohne Datum ODER ohne Uhrzeit gibt es keinen Beginn. */
  protected readonly imRueckstand = computed(() => {
    const v = this.werte();
    return !v.start_datum || !v.start_zeit;
  });

  constructor() {
    // Bestehender Kontakt gewählt → Vor-/Nachname entfallen.
    this.form.controls.existing_party_id.valueChanges
      .pipe(takeUntilDestroyed())
      .subscribe((val) => {
        const gewaehlt = !!val;
        this.bestehenderKontaktId.set(val ?? '');
        for (const f of [this.form.controls.first_name, this.form.controls.last_name]) {
          if (gewaehlt) f.clearValidators();
          else f.setValidators([Validators.required, Validators.maxLength(200)]);
          f.updateValueAndValidity({ emitEvent: false });
        }
        // Auch die E-Mail: Das Feld verschwindet mit dem Block. Bliebe
        // `Validators.email` daran hängen, wäre eine halb getippte Adresse ein
        // Fehler an einem unsichtbaren Feld — `absenden()` bräche wortlos ab
        // (WCAG 3.3.1: der Nutzer sieht nicht, was er beheben soll).
        const email = this.form.controls.email;
        if (gewaehlt) email.clearValidators();
        else email.setValidators([Validators.email]);
        email.updateValueAndValidity({ emitEvent: false });
      });

    // Bestehende Liegenschaft gewählt → Adressfelder entfallen.
    this.form.controls.existing_property_id.valueChanges
      .pipe(takeUntilDestroyed())
      .subscribe((val) => {
        const gewaehlt = !!val;
        this.bestehendeObjektId.set(val ?? '');
        for (const f of [
          this.form.controls.street,
          this.form.controls.postal_code,
          this.form.controls.city,
        ]) {
          if (gewaehlt) f.clearValidators();
          else f.setValidators([Validators.required]);
          f.updateValueAndValidity({ emitEvent: false });
        }
      });

    this.form.valueChanges
      .pipe(takeUntilDestroyed())
      .subscribe(() => this.pflichtfelderSynchronisieren());

    adressDublettenStrom(
      this.propertySvc,
      this.form.valueChanges.pipe(
        startWith(null),
        map(() => this.dublettenAbfrage()),
      ),
    )
      .pipe(takeUntilDestroyed())
      .subscribe((t) => this.dubletten.set(t));

    // Beim Öffnen zurücksetzen und mit dem angeklickten Slot vorbelegen.
    effect(() => {
      if (!this.offen()) return;
      this.formularMeldung.set(null);
      this.dubletten.set([]);
      this.vorlegen.set(false);
      this.form.reset({
        property_type: 'EINFAMILIENHAUS',
        priority: 'NORMAL',
        is_emergency: false,
        start_datum: this.startDatum(),
        start_zeit: this.startZeit(),
      });
      // Nach `reset` von Hand: Der Weg steht wieder auf „freigeben", und `reset`
      // allein räumt die Validatoren des Vorlege-Wegs nicht ab.
      this.pflichtfelderSynchronisieren();
      this.gewaehlteMitarbeiter.set([...this.mitarbeiterVorauswahl()]);
      this.stammdatenLaden();
    });
  }

  /**
   * Hält die wegabhängigen Pflichtfelder an ihren Controls aktuell.
   *
   * Zwei Felder wechseln je nach Ausgang die Pflicht: Der Verantwortungsbereich
   * ist beim Freigeben nötig (sonst scheitert die Freigabe erst am Server mit
   * 422), beim Vorlegen nicht. Die Frage an den Entscheider ist umgekehrt nur
   * beim Vorlegen Pflicht — ohne sie weiß der Chef nicht, worüber er entscheiden
   * soll, und der Server weist es ohnehin ab.
   *
   * Bewusst eine Methode statt einer reinen `valueChanges`-Reaktion: Der Wechsel
   * des Wegs ändert KEINEN Formularwert, würde also nie einfeuern. Beide Aufrufer
   * (Wertstrom und Weg-Umschaltung) müssen dieselbe Wahrheit herstellen.
   */
  private pflichtfelderSynchronisieren(): void {
    const paare: [typeof this.form.controls.responsibility_scope, boolean][] = [
      [this.form.controls.responsibility_scope, this.bereichNoetig()],
      [this.form.controls.vorlage_frage, this.vorlegen()],
    ];
    for (const [f, noetig] of paare) {
      const hatPflicht = f.hasValidator(Validators.required);
      if (noetig === hatPflicht) continue; // nichts zu tun — kein Rekursionsrisiko
      if (noetig) f.setValidators([Validators.required]);
      else f.clearValidators();
      f.updateValueAndValidity({ emitEvent: false });
    }
  }

  /**
   * Gewerke und Mitarbeiter sind Beiwerk: Fehlen sie (keine Stammdaten, 403 auf
   * dem Read-Endpunkt), bleibt die Auswahl leer und das Anlegen funktioniert
   * trotzdem. Deshalb kein Fehlerband, nur ein stiller Rückfall.
   */
  private stammdatenLaden(): void {
    // `false`: Alt-Gewerke wurden deaktiviert statt gelöscht und gehören nicht
    // in eine Neuanlage. Der Default des Service ist `true` — hier falsch.
    this.firmaSvc.listTrades(false).subscribe({
      next: (liste) =>
        this.gewerkOptionen.set(liste.map((t) => ({ wert: t.id, label: t.label }))),
      error: () => this.gewerkOptionen.set([]),
    });
    this.svc.listUsers().subscribe({
      next: (liste) =>
        this.mitarbeiter.set(liste.map((u) => ({ id: u.id, name: u.display_name }))),
      error: () => this.mitarbeiter.set([]),
    });
  }

  private dublettenAbfrage(): AdressDublettenQuery | null {
    const v = this.form.getRawValue();
    if (v.existing_property_id) return null;
    const street = v.street.trim();
    const postal_code = v.postal_code.trim();
    const city = v.city.trim();
    if (!street || (!postal_code && !city)) return null;
    return {
      street,
      house_number: v.house_number.trim() || null,
      postal_code: postal_code || null,
      city: city || null,
    };
  }

  protected uebernehmen(t: AdressTreffer): void {
    this.objektWahl()?.auswahlSetzen(propertyRefOption(t.property));
  }

  protected istGewaehlt(id: string): boolean {
    return this.gewaehlteMitarbeiter().includes(id);
  }

  protected mitarbeiterUmschalten(id: string): void {
    const liste = this.gewaehlteMitarbeiter();
    this.gewaehlteMitarbeiter.set(
      liste.includes(id) ? liste.filter((x) => x !== id) : [...liste, id],
    );
  }

  protected schliessen(): void {
    if (this.laedt()) return;
    this.abbrechen.emit();
  }

  /**
   * Abschluss auf einem der beiden Wege.
   *
   * Der Weg wird ZUERST gesetzt und die Pflichtfelder daran ausgerichtet, erst
   * danach validiert. Sonst prüfte die Validierung gegen den alten Weg: Der erste
   * Klick auf „Zur Entscheidung vorlegen" liefe gegen einen noch geforderten
   * Verantwortungsbereich, dessen Feld in diesem Moment schon ausgeblendet ist —
   * genau der wortlose Abbruch an einem unsichtbaren Feld, den die Umschaltung
   * bei Kontakt und Objekt oben vermeidet (WCAG 3.3.1).
   *
   * Ist die Frage an den Entscheider noch leer, ist der erste Klick deshalb kein
   * Fehlschlag, sondern die Aufforderung: Das Feld erscheint, wird rot markiert,
   * und die Meldung sagt, was fehlt.
   */
  protected absenden(vorlegen = false): void {
    if (this.laedt()) return;
    this.vorlegen.set(vorlegen);
    this.pflichtfelderSynchronisieren();
    serverFehlerZuruecksetzen(this.form);
    this.formularMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.form);
    if (this.form.invalid) {
      // Rückfallmeldung: Normalerweise trägt das rot markierte Feld die
      // Erklärung. Sollte der Fehler an einem gerade ausgeblendeten Control
      // hängen, bliebe der Knopf sonst wirkungslos ohne jede Rückmeldung.
      this.formularMeldung.set(
        vorlegen && !this.form.controls.vorlage_frage.value.trim()
          ? 'Bitte kurz formulieren, was entschieden werden soll — sonst weiß der Entscheider nicht, worum es geht.'
          : 'Bitte die rot markierten Felder prüfen — es fehlt noch eine Angabe.',
      );
      return;
    }

    const v = this.form.getRawValue();
    const kontakt = this.bestehenderKontaktId();
    const objekt = this.bestehendeObjektId();

    // Ohne Datum ODER ohne Uhrzeit gibt es keinen Beginn — der Termin landet
    // dann im Rückstand. Ein Datum ohne Uhrzeit als 00:00 zu deuten wäre falsch:
    // Niemand vereinbart einen Termin um Mitternacht.
    const start = this.imRueckstand()
      ? null
      : vonLokalerEingabe(`${v.start_datum}T${v.start_zeit}`);
    // Liegt das Ende VOR dem Beginn, ist der Nachtdienst über Mitternacht
    // gemeint (22:00–02:00) — die Plantafel stellt solche Termine dar. Das als
    // Eingabefehler abzuweisen hieße, eine gültige Schicht nicht erfassen zu
    // können; stattdessen rutscht das Ende auf den Folgetag.
    const endeDatum =
      v.end_zeit && v.end_zeit < v.start_zeit ? naechsterTag(v.start_datum) : v.start_datum;
    const ende =
      start && v.end_zeit ? vonLokalerEingabe(`${endeDatum}T${v.end_zeit}`) : null;

    if (start && ende && ende <= start) {
      this.formularMeldung.set('Das Ende muss nach dem Beginn liegen.');
      return;
    }

    const payload: AnrufIn = {
      person: kontakt
        ? { existing_party_id: kontakt }
        : {
            salutation: v.salutation.trim() || null,
            first_name: v.first_name.trim(),
            last_name: v.last_name.trim(),
            phone: v.phone.trim() || null,
            email: v.email.trim() || null,
          },
      property: objekt
        ? { existing_property_id: objekt }
        : {
            property_type: v.property_type,
            street: v.street.trim(),
            house_number: v.house_number.trim() || null,
            postal_code: v.postal_code.trim(),
            city: v.city.trim(),
          },
      auftrag: {
        title: v.title.trim(),
        description: v.description.trim() || null,
        priority: v.priority,
        is_emergency: v.is_emergency,
        // Leer lassen, wo ableitbar — der Server setzt beim EFH selbst.
        responsibility_scope: v.responsibility_scope || null,
        trade_id: v.trade_id || null,
        vorlegen,
        // Nur auf dem Vorlege-Weg mitschicken: Eine Frage an einem freigegebenen
        // Auftrag hätte keinen Statuswechsel, an dem sie hängen könnte.
        vorlage_frage: vorlegen ? v.vorlage_frage.trim() : null,
      },
      termin: {
        scheduled_start: start,
        scheduled_end: ende,
        assignee_ids: this.gewaehlteMitarbeiter(),
        access_instructions: v.access_instructions.trim() || null,
      },
    };

    this.laedt.set(true);
    this.svc.anruf(payload).subscribe({
      next: (res) => {
        this.laedt.set(false);
        this.angelegt.emit(res);
      },
      error: (err: HttpErrorResponse) => {
        this.laedt.set(false);
        this.formularMeldung.set(apiFehlerZuweisen(err, this.form).formular);
      },
    });
  }
}
