import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { map } from 'rxjs';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { DokumentBlatt } from '../../shared/dokument-blatt/dokument-blatt';
import { BelegService } from '../../core/beleg.service';
import { MailService } from '../../core/mail.service';
import { PartyService } from '../../core/party.service';
import { AuthService } from '../../core/auth.service';
import {
  BillingSource,
  InvoiceDetail,
  InvoicePartyCreate,
  InvoiceStatus,
  InvoiceType,
  LineType,
  QuoteLineInput,
} from '../../core/beleg.model';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { Dateien } from '../../shared/dateien/dateien';
import { ZielFilter } from '../../core/datei.model';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import { apiZuDeAnzeige, deZuApiDezimal, dezimalValidator } from '../../shared/formular/dezimal';
import { fristAbgelaufen, isoDatumDe } from '../../shared/datum';
import { dateiDownloadAusloesen } from '../../shared/datei-download';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';
import { VerbotenState, fehlerDetail, fehlerState, istVerboten } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: InvoiceDetail }
  | VerbotenState
  | { kind: 'error' };

type Meldung = { art: 'erfolg' | 'fehler'; text: string };

@Component({
  selector: 'app-rechnung-detail',
  imports: [Mappe, RouterLink, KeinZugriff, Bestaetigung, Dateien, ReactiveFormsModule, Dialog, Feld, ReferenzWahl, DokumentBlatt],
  templateUrl: './rechnung-detail.html',
  styleUrl: './rechnung-detail.scss',
})
export class RechnungDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(BelegService);
  private readonly mailSvc = inject(MailService);
  private readonly partySvc = inject(PartyService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly tab = signal('positionen');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  // --- Veröffentlichen (unumkehrbar) --------------------------------------
  protected readonly darfFreigeben = computed(() => this.auth.darf('invoicing', 'FREIGEBEN'));
  protected readonly meldung = signal<Meldung | null>(null);
  protected readonly publishOffen = signal(false);
  protected readonly publishLaedt = signal(false);

  /** Nur Entwürfe lassen sich veröffentlichen (Server setzt die Tore durch). */
  protected readonly kannVeroeffentlichen = computed(() => this.daten()?.status === 'ENTWURF');

  // --- Beteiligten hinzufügen (nur im Entwurf) ----------------------------
  protected readonly darfAendern = computed(() => this.auth.darf('invoicing', 'AENDERN'));
  /**
   * Positionen im Beleg-Editor bearbeiten — nur Entwurf + Recht invoicing/AENDERN
   * **und nur ohne aktive Abrechnungsbindung**.
   *
   * Der DB-Trigger `invoicing.protect_billed_invoice_lines` weist seit Migration
   * 0088 UPDATE und DELETE **einer gebundenen Zeile** ab (das INSERT einer neuen
   * Zeile dagegen nicht). Der Editor ersetzt den Positionssatz per Delete+Insert
   * und trifft dabei zwangsläufig die gebundene Zeile; er liefe also unweigerlich
   * in einen 422. Ihn anzubieten hieße, den Nutzer in eine Sackgasse zu schicken.
   * Ergänzt wird ein gebundener Entwurf über „Position anhängen"; der Ausweg aus
   * einem verunglückten Lauf bleibt „Bindungen lösen".
   */
  protected readonly darfBearbeiten = computed(
    () =>
      this.daten()?.status === 'ENTWURF' &&
      !this.daten()?.gebunden &&
      this.auth.darf('invoicing', 'AENDERN'),
  );

  // --- Abrechnungsbindungen (Migration 0084) -------------------------------
  /** Der Notausgang verlangt STORNIEREN — wie Storno/Gutschrift. */
  protected readonly darfEntbinden = computed(() => this.auth.darf('invoicing', 'STORNIEREN'));
  /** Nur ein gebundener ENTWURF lässt sich entbinden (veröffentlicht → stornieren). */
  protected readonly kannEntbinden = computed(
    () => !!this.daten()?.gebunden && this.daten()?.status === 'ENTWURF',
  );
  protected readonly loesenOffen = signal(false);
  protected readonly loesenLaedt = signal(false);

  loesenFragen(): void {
    this.meldung.set(null);
    this.loesenOffen.set(true);
  }
  loesenAbbrechen(): void {
    if (!this.loesenLaedt()) this.loesenOffen.set(false);
  }
  loesenBestaetigen(grund: string | null): void {
    const d = this.daten();
    if (!d || this.loesenLaedt() || !grund) return;
    this.loesenLaedt.set(true);
    this.svc.bindungenLoesen(d.id, grund).subscribe({
      next: (aktualisiert) => {
        this.loesenLaedt.set(false);
        this.loesenOffen.set(false);
        this.state.set({ kind: 'ready', data: aktualisiert });
        this.meldung.set({
          art: 'erfolg',
          text:
            'Bindungen gelöst. Die gebundenen Positionen wurden aus dem Entwurf entfernt; ' +
            'Berichtspositionen, Zeiten bzw. Angebotspositionen sind wieder abrechenbar.',
        });
      },
      error: (err) => {
        this.loesenLaedt.set(false);
        this.loesenOffen.set(false);
        this.meldung.set({ art: 'fehler', text: this.fehlerText(err) });
      },
    });
  }

  // --- Position anhängen (der Weg für den GEBUNDENEN Entwurf) --------------
  // Der Beleg-Editor ersetzt den ganzen Positionssatz per Delete+Insert und läuft
  // damit gegen die gebundene Zeile (422). Das **INSERT einer neuen** Zeile lässt
  // die DB dagegen ausdrücklich zu (Migration 0088) — Anfahrtspauschale,
  // Rabattzeile, Zusatztext. Deshalb dieser schmale Weg statt der Notbremse.
  protected readonly zeileOffen = signal(false);
  protected readonly zeileLaedt = signal(false);
  protected readonly zeileMeldung = signal<string | null>(null);
  protected readonly zeileEntfernenLaedt = signal(false);

  protected readonly lineTypOptionen: FeldOption[] = [
    { wert: 'MATERIAL', label: 'Material' },
    { wert: 'ARBEITSZEIT', label: 'Arbeitszeit' },
    { wert: 'PAUSCHALE', label: 'Pauschale' },
    { wert: 'FREMDLEISTUNG', label: 'Fremdleistung' },
    { wert: 'FAHRT', label: 'Fahrt' },
    { wert: 'ZUSCHLAG', label: 'Zuschlag' },
    { wert: 'TEXT', label: 'Textzeile (ohne Betrag)' },
  ];
  protected readonly taxCodeOptionen: FeldOption[] = [
    { wert: 'DE_19', label: 'USt 19 %' },
    { wert: 'DE_7', label: 'USt 7 %' },
    { wert: 'DE_0', label: 'Steuerfrei (0 %)' },
    { wert: 'DE_13B', label: '§ 13b UStG (Reverse Charge)' },
  ];

  protected readonly zeileForm = this.fb.group({
    line_type: this.fb.control<LineType>('PAUSCHALE', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    description: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    // Menge und Einzelpreis sind im Dialog als Pflicht ausgezeichnet (`[pflicht]`
    // → `aria-required`) — dann muss die Validierung das auch durchsetzen: der
    // `dezimalValidator` allein lässt das leere Feld durch, und der Server nähme es
    // erst als 422 zurück. Bei einer **Textzeile** tragen beide Felder keinen Wert
    // (sie sind ausgeblendet); dort wird `required` zur Laufzeit wieder entfernt.
    quantity: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, dezimalValidator],
    }),
    unit: this.fb.control('', { nonNullable: true }),
    unit_price: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, dezimalValidator],
    }),
    discount_percent: this.fb.control('', {
      nonNullable: true,
      validators: [dezimalValidator],
    }),
    tax_code: this.fb.control('DE_19', { nonNullable: true }),
    // § 35a — dieselbe Konvention wie im Beleg-Editor: ohne Häkchen leitet der
    // Server den Anteil aus der Positionsart ab (ARBEITSZEIT/FAHRT voll, MATERIAL
    // 0,00, sonst UNBESTIMMT). Mit Häkchen gilt der eingetragene Betrag.
    labour_manual: this.fb.control(false, { nonNullable: true }),
    labour_net_amount: this.fb.control('', {
      nonNullable: true,
      validators: [dezimalValidator],
    }),
  });

  /** Signal-Spiegel der Positionsart (das Formular selbst ist kein Signal). */
  protected readonly zeileTyp = signal<LineType>('PAUSCHALE');
  /** Text-/Zwischensummenzeilen tragen keinen Betrag (Server weist ihn ab). */
  protected readonly zeileIstText = computed(() => this.isText(this.zeileTyp()));

  /**
   * Ob der Server den § 35a-Anteil aus der Positionsart ABLEITEN kann.
   * ARBEITSZEIT/FAHRT sind voll begünstigt, MATERIAL gar nicht — bei PAUSCHALE,
   * FREMDLEISTUNG und ZUSCHLAG bleibt er **unbestimmt**, bis ihn jemand setzt.
   */
  protected readonly zeileLohnAbleitbar = computed(() => {
    const t = this.zeileTyp();
    return t === 'ARBEITSZEIT' || t === 'FAHRT' || t === 'MATERIAL';
  });

  /**
   * Der Anteil MUSS angegeben werden — sonst zerstört die angehängte Zeile den
   * bestehenden § 35a-Ausweis der Rechnung.
   *
   * Der Ausweis ist eine Aussage über den **ganzen Beleg**: eine einzige Position
   * ohne bestimmten Anteil macht ihn unbestimmbar (`OFFENE_POSITIONEN`), und dann
   * weist die Rechnung gar keine Arbeitskosten mehr aus — der Privatkunde verliert
   * 20 % Steuerermäßigung auf ALLES. Genau das droht hier: der Dialog bewirbt die
   * „Anfahrtspauschale", und `PAUSCHALE` ist die Voreinstellung.
   *
   * Deshalb Pflichtfeld statt bloßem Hinweis — aber nur, wo wirklich etwas kaputt
   * gehen kann: Die Rechnung weist aus (`show_labour_costs`) UND ihr Ausweis ist
   * heute bestimmbar. Ist er ohnehin schon offen oder abgeschaltet, bleibt die
   * Angabe freiwillig (das Häkchen wie im Editor).
   *
   * Wer den Anteil nicht kennt (z. B. eine Fremdleistung, deren Aufteilung der
   * Subunternehmer noch nicht geliefert hat), hat einen ausdrücklichen Ausweg: den
   * Ausweis in der Übersicht abschalten. Das ist eine bewusste Entscheidung — und
   * genau das ist der Punkt. Eine „0" vorauszufüllen wäre keine: sie behauptete,
   * die Position enthalte keine Arbeitskosten, ohne dass es jemand gesagt hat.
   */
  protected readonly zeileLohnPflicht = computed(() => {
    const d = this.daten();
    if (!d || this.zeileIstText() || this.zeileLohnAbleitbar()) return false;
    return !!d.show_labour_costs && !!d.arbeitskosten?.bestimmbar;
  });

  /** Zeigt die Betragseingabe (Pflichtangabe oder freiwillig per Häkchen). */
  protected zeileLohnBetragSichtbar(): boolean {
    if (this.zeileIstText()) return false;
    return this.zeileLohnPflicht() || this.zeileForm.controls.labour_manual.value;
  }

  /**
   * Was der Server ohne Angabe ableiten wird — als Hinweis im Dialog (er RECHNET
   * damit nichts, er sagt nur an, was passiert). Methode statt `computed`: liest
   * einen FormControl-Wert, und der ist kein Signal.
   */
  protected zeileLohnHinweis(): string | null {
    if (this.zeileIstText() || this.zeileForm.controls.labour_manual.value) return null;
    switch (this.zeileTyp()) {
      case 'ARBEITSZEIT':
      case 'FAHRT':
        return 'Zählt in voller Höhe als Arbeitskosten (§ 35a EStG).';
      case 'MATERIAL':
        return 'Zählt nicht als Arbeitskosten (Material ist nicht begünstigt).';
      default:
        return (
          'Arbeitskostenanteil unbestimmt: Diese Rechnung weist ohnehin keine ' +
          'Arbeitskosten nach § 35a aus, daran ändert die Position nichts.'
        );
    }
  }

  /** Angehängt werden darf nur an einen Entwurf — angeboten, wo der Editor zu ist. */
  protected readonly kannZeileAnhaengen = computed(
    () => this.daten()?.status === 'ENTWURF' && !!this.daten()?.gebunden && this.darfAendern(),
  );
  /**
   * Die letzte Zeile lässt sich nur zurücknehmen, wenn sie NICHT gebunden ist — und
   * nicht die **Anrechnung** einer Abschlagsrechnung: die gehört zur Verkettung und
   * wird über die Abschlagszuordnung gepflegt (der Server weist sie mit 422 ab).
   */
  protected readonly kannLetzteEntfernen = computed(() => {
    const d = this.daten();
    if (!d || d.status !== 'ENTWURF' || !d.gebunden || !this.darfAendern()) return false;
    const letzte = d.lines[d.lines.length - 1];
    return !!letzte && !letzte.billing_source && !letzte.advance_invoice_id;
  });

  zeileOeffnen(): void {
    this.zeileForm.reset({
      line_type: 'PAUSCHALE',
      description: '',
      quantity: '',
      unit: '',
      unit_price: '',
      discount_percent: '',
      tax_code: 'DE_19',
      // Kein Vorbelegen mit 0: ein stiller Nullwert behauptete „keine
      // Arbeitskosten", ohne dass es jemand gesagt hätte.
      labour_manual: false,
      labour_net_amount: '',
    });
    this.zeileTyp.set('PAUSCHALE');
    this.betragsfelderPflicht(true);
    this.lohnfeldPflicht();
    this.zeileMeldung.set(null);
    this.meldung.set(null);
    this.zeileOffen.set(true);
  }

  zeileSchliessen(): void {
    if (!this.zeileLaedt()) this.zeileOffen.set(false);
  }

  zeileAbsenden(): void {
    const d = this.daten();
    if (!d || this.zeileLaedt()) return;
    serverFehlerZuruecksetzen(this.zeileForm);
    this.zeileMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.zeileForm);
    if (this.zeileForm.invalid) return;

    const v = this.zeileForm.getRawValue();
    const text = this.isText(v.line_type);
    // § 35a: Der Betrag geht nur mit, wenn er ausdrücklich angegeben wurde —
    // sonst `null` = „nichts gesagt", und der Server leitet ab (bzw. lässt den
    // Anteil unbestimmt). Eine Textzeile trägt keinen Betrag und damit auch
    // keinen Anteil (der Server weist ihn ab).
    const manuell = v.labour_manual || this.zeileLohnPflicht();
    // Dezimalfelder gehen als Punkt-String hinaus (deZuApiDezimal) — nie als
    // JS-number. Gerechnet wird ausschließlich auf dem Server.
    const payload: QuoteLineInput = text
      ? { line_type: v.line_type, description: v.description.trim() }
      : {
          line_type: v.line_type,
          description: v.description.trim(),
          quantity: deZuApiDezimal(v.quantity) || null,
          unit: v.unit.trim() || null,
          unit_price: deZuApiDezimal(v.unit_price) || null,
          discount_percent: deZuApiDezimal(v.discount_percent) || null,
          tax_code: v.tax_code,
          labour_net_amount: manuell ? deZuApiDezimal(v.labour_net_amount) || null : null,
        };

    this.zeileLaedt.set(true);
    this.svc.addInvoiceLine(d.id, payload).subscribe({
      next: (aktualisiert) => {
        this.zeileLaedt.set(false);
        this.zeileOffen.set(false);
        this.state.set({ kind: 'ready', data: aktualisiert });
        this.meldung.set({ art: 'erfolg', text: 'Position wurde angehängt.' });
      },
      error: (err) => {
        this.zeileLaedt.set(false);
        this.zeileMeldung.set(apiFehlerZuweisen(err, this.zeileForm).formular);
      },
    });
  }

  letzteEntfernen(): void {
    const d = this.daten();
    if (!d || this.zeileEntfernenLaedt()) return;
    this.zeileEntfernenLaedt.set(true);
    this.meldung.set(null);
    this.svc.removeLastInvoiceLine(d.id).subscribe({
      next: (aktualisiert) => {
        this.zeileEntfernenLaedt.set(false);
        this.state.set({ kind: 'ready', data: aktualisiert });
        this.meldung.set({ art: 'erfolg', text: 'Letzte Position wurde entfernt.' });
      },
      error: (err) => {
        this.zeileEntfernenLaedt.set(false);
        this.meldung.set({ art: 'fehler', text: this.fehlerText(err) });
      },
    });
  }

  /** Herkunft einer gebundenen Position im Klartext (nie nur Farbe). */
  bindungLabel(q: BillingSource | null | undefined): string | null {
    if (!q) return null;
    const map: Record<BillingSource, string> = {
      BERICHTSPOSITION: 'aus Baustellenbericht',
      ZEITBUCHUNG: 'aus Zeiterfassung',
      ANGEBOTSPOSITION: 'aus Angebot',
    };
    return map[q] ?? q;
  }
  /** Beteiligte lassen sich nur am Entwurf ergänzen (Server erzwingt es). */
  protected readonly kannBeteiligen = computed(() => this.daten()?.status === 'ENTWURF');
  protected readonly beteiligtOffen = signal(false);
  protected readonly beteiligtLaedt = signal(false);
  protected readonly beteiligtMeldung = signal<string | null>(null);
  protected readonly rollen: FeldOption[] = [
    { wert: 'INVOICE_DEBTOR', label: 'Rechnungsschuldner' },
    { wert: 'INVOICE_RECIPIENT', label: 'Rechnungsempfänger' },
    { wert: 'REPRESENTATIVE', label: 'Vertretung' },
    { wert: 'COST_BEARER', label: 'Kostenträger' },
  ];
  protected readonly beteiligtForm = this.fb.group({
    party_id: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    role: this.fb.control('INVOICE_DEBTOR', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    is_primary: this.fb.control(false, { nonNullable: true }),
  });

  /** Kontaktsuche (Personen und Organisationen) für den Beteiligten. */
  protected readonly kontaktSuche: RefSuche = (q) =>
    this.partySvc.list({ page: 1, page_size: 20, q }).pipe(
      map((p) => p.items.map((o) => ({ id: o.id, label: o.display_name }))),
    );

  // --- Per E-Mail senden (nur veröffentlicht) -----------------------------
  protected readonly darfVersenden = computed(() => this.auth.darf('invoicing', 'VERSENDEN'));
  /** Nur veröffentlichte Rechnungen lassen sich versenden (Server erzwingt es). */
  protected readonly kannVersenden = computed(() => this.daten()?.status === 'VEROEFFENTLICHT');
  /** Ob ein Absenderkonto hinterlegt ist (null = noch nicht geladen). Der Server
   *  bleibt maßgeblich; das UI blendet die Aktion ohne Konto nur aus/deaktiviert. */
  protected readonly mailKontoVorhanden = signal<boolean | null>(null);
  protected readonly versandOffen = signal(false);
  protected readonly versandLaedt = signal(false);
  protected readonly versandMeldung = signal<string | null>(null);
  protected readonly versandForm = this.fb.group({
    to_address: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.email],
    }),
  });

  protected readonly tabs: MappeTab[] = [
    { id: 'positionen', label: 'Positionen' },
    { id: 'beteiligte', label: 'Beteiligte' },
    { id: 'uebersicht', label: 'Übersicht' },
    { id: 'dateien', label: 'Dateien' },
  ];

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  /** Stabile Zielreferenz fuer den Dateien-Tab (nur bei Rechnungswechsel neu). */
  protected readonly dateienZiel = computed<ZielFilter>(() => ({
    invoice_id: this.daten()?.id ?? '',
  }));

  /** Zahlungsbedingungen als Klartext (nie nur Farbe/Zahl). Alle Beträge und
   * Fristen kommen vom Server — hier wird nichts nachgerechnet. */
  protected readonly zahlungsbedingungen = computed<string | null>(() => {
    const d = this.daten();
    if (!d) return null;
    if (d.skonto_bis && d.skonto_betrag) {
      const satz = apiZuDeAnzeige(d.discount_percent, 2);
      // Abgelaufene Frist im Klartext benennen (nicht nur farblich), sonst liest
      // sich ein Monate alter Beleg wie ein noch einlösbarer Skontoabzug.
      const abgelaufen = fristAbgelaufen(d.skonto_bis) ? ' (Frist abgelaufen)' : '';
      const kern =
        `${satz} % Skonto bei Zahlung bis ${this.datumDe(d.skonto_bis)}` +
        `${abgelaufen} (${this.euro(d.skonto_betrag)})`;
      return d.due_date
        ? `${kern}, sonst netto bis ${this.datumDe(d.due_date)}.`
        : `${kern}, sonst netto ohne Abzug.`;
    }
    if (d.due_date) return `Zahlbar ohne Abzug bis ${this.datumDe(d.due_date)}.`;
    if (d.payment_term_days !== null) {
      return `${d.payment_term_days} Tage netto ab Rechnungsdatum.`;
    }
    return null;
  });

  /** ISO-Datum (JJJJ-MM-TT) deutsch, ohne Zeitzonen-Drift durch `new Date()`. */
  private datumDe(iso: string): string {
    return isoDatumDe(iso);
  }

  /** ISO-Datum deutsch (oder „—"), für die Verkettungs-Stempel. */
  isoDatum(iso: string | null): string {
    return iso ? isoDatumDe(iso) : '—';
  }

  /**
   * Angerechnete Summe (brutto) einer Schlussrechnung — reine ANZEIGE aus den vom
   * Server eingefrorenen Beträgen. Der Zahlbetrag selbst wird NICHT hier
   * gerechnet: er steht als `gross_total` am Beleg (der Server ist die
   * verbindliche Rechenstelle).
   */
  protected readonly anrechnungBrutto = computed<string | null>(() => {
    const d = this.daten();
    if (!d || d.advances.length === 0) return null;
    const summe = d.advances.reduce((s, a) => s + Number(a.gross_amount), 0);
    return summe.toFixed(2);
  });

  /**
   * § 35a: Fehlt an mindestens einer Position der Arbeitskostenanteil, weist die
   * Rechnung KEINE Arbeitskosten aus — der Privatkunde verliert damit 20 %
   * Steuerermäßigung darauf, und nach dem Veröffentlichen ist der Beleg
   * unveränderlich (GoBD). Deshalb warnt die Mappe, solange der Entwurf noch
   * änderbar ist. Die Positionsnummern kommen vom Server.
   */
  protected readonly lohnWarnung = computed<string | null>(() => {
    const d = this.daten();
    if (!d || !d.show_labour_costs) return null;
    const ak = d.arbeitskosten;
    if (!ak || ak.bestimmbar) return null;

    if (ak.grund === 'UNSTIMMIG') {
      // Kein Bedienfehler-Vorwurf: Das entsteht auch bei völlig korrekt erfassten
      // Abschlägen (z. B. ein reiner Materialvorschuss). Der Ausweis wäre dann
      // negativ oder größer als der Rechnungsbetrag — beides darf nicht auf einem
      // Beleg stehen, also weist er nichts aus.
      return (
        'Der angerechnete Abschlag trägt andere Arbeitskosten, als diese Rechnung ' +
        'insgesamt abrechnet: Der Ausweis wäre negativ oder größer als der ' +
        'Rechnungsbetrag — „darin enthalten" träfe dann nicht zu. Diese Rechnung ' +
        'weist deshalb keine Arbeitskosten nach § 35a EStG aus. Die Beträge der ' +
        'Rechnung selbst sind davon unberührt.'
      );
    }
    if (ak.offen.length === 0) return null;

    // Eine Anrechnungsposition stammt aus einem veröffentlichten Abschlag: sie
    // steht nicht im Editor und lässt sich nicht nachtragen. Den Bediener dorthin
    // zu schicken, wäre eine Sackgasse.
    const anrechnung = new Set(
      d.lines.filter((l) => l.advance_invoice_id).map((l) => l.position_number),
    );
    const nachtragbar = ak.offen.filter((n) => !anrechnung.has(n));
    const ausAbschlag = ak.offen.filter((n) => anrechnung.has(n));

    const teile: string[] = [];
    if (nachtragbar.length > 0) {
      teile.push(
        `Für ${nachtragbar.length === 1 ? 'Position' : 'die Positionen'} ` +
          `${nachtragbar.join(', ')} ist der Arbeitskostenanteil nicht angegeben.`,
      );
    }
    if (ausAbschlag.length > 0) {
      teile.push(
        `${ausAbschlag.length === 1 ? 'Position' : 'Die Positionen'} ` +
          `${ausAbschlag.join(', ')} rechnet einen Abschlag an, der selbst keinen ` +
          'Arbeitskostenanteil ausweist — er ist veröffentlicht und nicht mehr änderbar.',
      );
    }
    teile.push(
      'Solange etwas davon offen ist, weist die Rechnung keine Arbeitskosten nach ' +
        '§ 35a EStG aus.',
    );
    return teile.join(' ');
  });

  /** Ob die offene Lücke überhaupt im Editor behebbar ist (siehe `lohnWarnung`). */
  protected readonly lohnLueckeNachtragbar = computed<boolean>(() => {
    const d = this.daten();
    const ak = d?.arbeitskosten;
    if (!d || !ak || ak.grund !== 'OFFENE_POSITIONEN') return false;
    const anrechnung = new Set(
      d.lines.filter((l) => l.advance_invoice_id).map((l) => l.position_number),
    );
    return ak.offen.some((n) => !anrechnung.has(n));
  });

  /** Ob der § 35a-Block auf dem Beleg steht (bestimmbar UND Betrag > 0). */
  protected readonly zeigtArbeitskosten = computed<boolean>(() => {
    const d = this.daten();
    const ak = d?.arbeitskosten;
    return !!d?.show_labour_costs && !!ak?.bestimmbar && Number(ak.gross_amount) !== 0;
  });

  protected readonly lohnSchalterLaedt = signal(false);

  /** § 35a-Ausweis je Beleg ein-/ausschalten (nur im Entwurf; B2B braucht ihn nicht). */
  lohnAusweisSetzen(an: boolean): void {
    const d = this.daten();
    if (!d || this.lohnSchalterLaedt()) return;
    this.lohnSchalterLaedt.set(true);
    this.svc.updateInvoice(d.id, { show_labour_costs: an }).subscribe({
      next: (neu) => {
        this.state.set({ kind: 'ready', data: neu });
        this.lohnSchalterLaedt.set(false);
        this.meldung.set({
          art: 'erfolg',
          text: an
            ? 'Arbeitskosten nach § 35a werden auf der Rechnung ausgewiesen.'
            : 'Arbeitskosten nach § 35a werden nicht ausgewiesen.',
        });
      },
      error: (err) => {
        this.lohnSchalterLaedt.set(false);
        this.meldung.set({ art: 'fehler', text: this.fehlerText(err) });
      },
    });
  }

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('positionen');
      if (!id) {
        this.state.set({ kind: 'error' });
        return;
      }
      this.load(id);
    });

    // Positionsart spiegeln: eine Textzeile trägt keinen Betrag, die Geldfelder
    // verschwinden dann aus dem Dialog (der Server weist sie ohnehin ab). Mit
    // ihnen fällt auch ihre Pflicht — ein ausgeblendetes Pflichtfeld wäre eine
    // unsichtbare Sackgasse.
    this.zeileForm.controls.line_type.valueChanges
      .pipe(takeUntilDestroyed())
      .subscribe((t) => {
        this.zeileTyp.set(t);
        this.betragsfelderPflicht(!this.isText(t));
        // Die Positionsart entscheidet, ob der Server den § 35a-Anteil ableiten
        // kann — und damit, ob er angegeben werden MUSS.
        this.lohnfeldPflicht();
      });

    // Häkchen „abweichend angeben" an → der Betrag ist Pflicht (sonst hielte sich
    // der Bediener für fertig, während der Server wieder ableitet).
    this.zeileForm.controls.labour_manual.valueChanges
      .pipe(takeUntilDestroyed())
      .subscribe(() => this.lohnfeldPflicht());

    // Ob ein Absenderkonto konfiguriert ist, entscheidet über die Versand-Aktion.
    // Nur laden, wenn die Rolle überhaupt versenden darf.
    if (this.darfVersenden()) {
      this.mailSvc.getAccount().subscribe({
        next: (k) => this.mailKontoVorhanden.set(k.exists),
        error: () => this.mailKontoVorhanden.set(false),
      });
    }
  }

  retry(): void {
    const id = this.route.snapshot.paramMap.get('id');
    if (id) this.load(id);
  }

  private load(id: string): void {
    const rid = ++this.reqId;
    this.state.set({ kind: 'loading' });
    this.svc.getInvoice(id).subscribe({
      next: (data) => {
        if (rid === this.reqId) this.state.set({ kind: 'ready', data });
      },
      error: (err) => {
        if (rid === this.reqId) this.state.set(fehlerState(err));
      },
    });
  }

  // ---- Veröffentlichen ----------------------------------------------------
  publishFragen(): void {
    this.meldung.set(null);
    this.publishOffen.set(true);
  }

  publishAbbrechen(): void {
    if (!this.publishLaedt()) this.publishOffen.set(false);
  }

  publishBestaetigen(): void {
    const d = this.daten();
    if (!d || this.publishLaedt()) return;
    this.publishLaedt.set(true);
    this.svc.publishInvoice(d.id).subscribe({
      next: (aktualisiert) => {
        this.publishLaedt.set(false);
        this.publishOffen.set(false);
        this.state.set({ kind: 'ready', data: aktualisiert });
        this.meldung.set({
          art: 'erfolg',
          text: `Rechnung veröffentlicht. Belegnummer ${aktualisiert.invoice_number ?? '—'} wurde vergeben.`,
        });
      },
      error: (err) => {
        this.publishLaedt.set(false);
        this.publishOffen.set(false);
        // Die DB-Tore liefern präzise 422-Meldungen — wörtlich zeigen.
        this.meldung.set({ art: 'fehler', text: this.fehlerText(err) });
      },
    });
  }

  // ---- Beteiligten hinzufügen ---------------------------------------------
  beteiligtOeffnen(): void {
    this.beteiligtForm.reset({ party_id: '', role: 'INVOICE_DEBTOR', is_primary: false });
    this.beteiligtMeldung.set(null);
    this.meldung.set(null);
    this.beteiligtOffen.set(true);
  }

  beteiligtSchliessen(): void {
    if (!this.beteiligtLaedt()) this.beteiligtOffen.set(false);
  }

  beteiligtAbsenden(): void {
    const d = this.daten();
    if (!d || this.beteiligtLaedt()) return;
    serverFehlerZuruecksetzen(this.beteiligtForm);
    this.beteiligtMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.beteiligtForm);
    if (this.beteiligtForm.invalid) return;

    const v = this.beteiligtForm.getRawValue();
    const payload: InvoicePartyCreate = {
      party_id: v.party_id,
      role: v.role,
      is_primary: v.is_primary,
    };
    this.beteiligtLaedt.set(true);
    this.svc.addInvoiceParty(d.id, payload).subscribe({
      next: (aktualisiert) => {
        this.beteiligtLaedt.set(false);
        this.beteiligtOffen.set(false);
        this.state.set({ kind: 'ready', data: aktualisiert });
        this.meldung.set({ art: 'erfolg', text: 'Beteiligter wurde ergänzt.' });
      },
      error: (err) => {
        this.beteiligtLaedt.set(false);
        this.beteiligtMeldung.set(apiFehlerZuweisen(err, this.beteiligtForm).formular);
      },
    });
  }

  // ---- Per E-Mail senden --------------------------------------------------
  versandOeffnen(): void {
    const d = this.daten();
    if (!d) return;
    this.versandForm.reset({ to_address: d.recipient_email ?? '' });
    serverFehlerZuruecksetzen(this.versandForm);
    this.versandMeldung.set(null);
    this.meldung.set(null);
    this.versandOffen.set(true);
  }

  versandSchliessen(): void {
    if (!this.versandLaedt()) this.versandOffen.set(false);
  }

  versandAbsenden(): void {
    const d = this.daten();
    if (!d || this.versandLaedt()) return;
    serverFehlerZuruecksetzen(this.versandForm);
    this.versandMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.versandForm);
    if (this.versandForm.invalid) return;

    const to = this.versandForm.controls.to_address.value.trim();
    this.versandLaedt.set(true);
    this.svc.sendInvoiceEmail(d.id, to).subscribe({
      next: (res) => {
        this.versandLaedt.set(false);
        this.versandOffen.set(false);
        this.meldung.set({
          art: 'erfolg',
          text: `Rechnung wurde als PDF an ${res.to_address} gesendet.`,
        });
      },
      error: (err) => {
        this.versandLaedt.set(false);
        this.versandMeldung.set(apiFehlerZuweisen(err, this.versandForm).formular);
      },
    });
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  private fehlerText(err: unknown): string {
    if (istVerboten(err)) return fehlerDetail(err) ?? 'Keine Berechtigung für diese Aktion.';
    return fehlerDetail(err) ?? 'Die Aktion ist fehlgeschlagen. Bitte erneut versuchen.';
  }

  // ---- Darstellungshelfer -------------------------------------------------
  euro(amount: string | null): string {
    if (amount === null) return '—';
    return new Intl.NumberFormat('de-DE', {
      style: 'currency',
      currency: 'EUR',
    }).format(Number(amount));
  }

  menge(qty: string | null, unit: string | null): string {
    if (qty === null) return '';
    const n = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 3 }).format(
      Number(qty),
    );
    return unit ? `${n} ${unit}` : n;
  }

  // ---- Dokumentansicht (Befund G1) ----------------------------------------

  /**
   * Der Informationsblock rechts neben dem Anschriftfeld (DIN 5008).
   *
   * Nur, was gepflegt ist: Ein Entwurf hat noch keine Belegnummer, und eine
   * Zeile „Rechnungs-Nr.: —" auf einem Schriftstück sieht nach Fehler aus,
   * nicht nach Entwurf.
   */
  protected readonly dokumentMeta = computed(() => {
    const d = this.daten();
    if (!d) return [];
    const zeilen: { label: string; wert: string }[] = [];
    if (d.invoice_number) zeilen.push({ label: 'Beleg-Nr.', wert: d.invoice_number });
    if (d.invoice_date) {
      zeilen.push({ label: 'Belegdatum', wert: this.isoDatum(d.invoice_date) });
    }
    if (d.due_date) zeilen.push({ label: 'Fällig bis', wert: this.isoDatum(d.due_date) });
    const objekt = [d.property.name, d.property.city].filter(Boolean).join(' · ');
    if (objekt) zeilen.push({ label: 'Objekt', wert: objekt });
    return zeilen;
  });

  /** Betreff: „Rechnung 2026-0042" bzw. die jeweilige Belegart. */
  protected readonly dokumentBetreff = computed(() => {
    const d = this.daten();
    if (!d) return '';
    return `${this.typeLabel(d.invoice_type)} ${d.invoice_number ?? ''}`.trim();
  });

  /**
   * Trägt das Blatt den Entwurfsaufdruck? Alles vor der Veröffentlichung — das
   * ist dieselbe Grenze, die auch das Vorschau-PDF zieht.
   */
  protected readonly istEntwurf = computed(() => this.daten()?.status === 'ENTWURF');

  typeLabel(t: InvoiceType): string {
    const map: Record<InvoiceType, string> = {
      RECHNUNG: 'Rechnung',
      ABSCHLAGSRECHNUNG: 'Abschlagsrechnung',
      TEILRECHNUNG: 'Teilrechnung',
      SCHLUSSRECHNUNG: 'Schlussrechnung',
      GUTSCHRIFT: 'Gutschrift',
      STORNO: 'Storno',
    };
    return map[t] ?? t;
  }

  statusLabel(s: InvoiceStatus): string {
    return s === 'VEROEFFENTLICHT' ? 'Veröffentlicht' : 'Entwurf';
  }
  statusClass(s: InvoiceStatus): string {
    return s === 'VEROEFFENTLICHT' ? 'stamp--positive' : '';
  }

  lineTypeLabel(t: LineType): string {
    const map: Record<LineType, string> = {
      MATERIAL: 'Material',
      ARBEITSZEIT: 'Arbeitszeit',
      PAUSCHALE: 'Pauschale',
      FREMDLEISTUNG: 'Fremdleistung',
      FAHRT: 'Fahrt',
      ZUSCHLAG: 'Zuschlag',
      TEXT: 'Text',
      ZWISCHENSUMME: 'Zwischensumme',
    };
    return map[t] ?? t;
  }
  isText(t: LineType): boolean {
    return t === 'TEXT' || t === 'ZWISCHENSUMME';
  }

  /**
   * Menge/Einzelpreis pflichtig schalten — genau dann, wenn sie im Dialog stehen.
   * Eine Textzeile trägt keinen Betrag; ihre Felder sind ausgeblendet und dürfen
   * das Formular nicht blockieren.
   */
  private betragsfelderPflicht(pflicht: boolean): void {
    for (const c of [this.zeileForm.controls.quantity, this.zeileForm.controls.unit_price]) {
      c.setValidators(pflicht ? [Validators.required, dezimalValidator] : [dezimalValidator]);
      c.updateValueAndValidity({ emitEvent: false });
    }
  }

  /**
   * Den § 35a-Betrag pflichtig schalten — genau dann, wenn er im Dialog steht:
   * erzwungen (`zeileLohnPflicht`) oder freiwillig per Häkchen. Ohne diesen
   * Validator liefe ein leeres Feld als „nichts angegeben" durch, und der Ausweis
   * der ganzen Rechnung fiele weg, ohne dass es jemand bemerkt.
   *
   * Wo die Angabe Pflicht ist, entfällt die Wahl: das Häkchen wird gesetzt (und
   * im Dialog gar nicht erst angeboten).
   */
  private lohnfeldPflicht(): void {
    const manuell = this.zeileForm.controls.labour_manual;
    if (this.zeileLohnPflicht() && !manuell.value) {
      manuell.setValue(true, { emitEvent: false });
    }
    const c = this.zeileForm.controls.labour_net_amount;
    c.setValidators(
      this.zeileLohnBetragSichtbar()
        ? [Validators.required, dezimalValidator]
        : [dezimalValidator],
    );
    c.updateValueAndValidity({ emitEvent: false });
  }

  roleLabel(r: string): string {
    const map: Record<string, string> = {
      INVOICE_DEBTOR: 'Rechnungsschuldner',
      INVOICE_RECIPIENT: 'Rechnungsempfänger',
      REPRESENTATIVE: 'Vertretung',
      COST_BEARER: 'Kostenträger',
    };
    return map[r] ?? r;
  }
  // Kurzform des Inhalts-Hashes (GoBD-Beleg-Fingerabdruck) für die Anzeige.
  hashKurz(h: string | null): string {
    return h ? h.slice(0, 12) + '…' : '—';
  }

  /** URL der on-the-fly gerenderten PDF-Ausfertigung (nur veröffentlicht). */
  pdfUrl(id: string): string {
    return `/api/invoicing/invoices/${id}/pdf`;
  }

  /** Entwurfsvorschau (jeder Status, ENTWURF-Aufdruck, wird nie archiviert). */
  vorschauUrl(id: string): string {
    return `/api/invoicing/invoices/${id}/pdf/vorschau`;
  }

  // --- E-Rechnung (ZUGFeRD/Factur-X) ---------------------------------------
  // Bewusst KEIN `window.open` auf die URL: der Endpunkt ist anmeldepflichtig,
  // und ein neues Fenster trägt weder den CSRF-Header noch verlässlich das
  // Session-Cookie. Der Download läuft daher als Blob durch den HttpClient
  // (Interceptor) und wird lokal als Datei ausgelöst.
  protected readonly eRechnungLaedt = signal(false);

  eRechnungHerunterladen(): void {
    const d = this.daten();
    if (!d || this.eRechnungLaedt()) return;
    this.eRechnungLaedt.set(true);
    this.meldung.set(null);
    this.svc.zugferdPdf(d.id).subscribe({
      next: (blob) => {
        this.eRechnungLaedt.set(false);
        const nummer = d.invoice_number ?? d.id;
        dateiDownloadAusloesen(blob, `${nummer}-zugferd.pdf`);
      },
      error: (err) => {
        this.eRechnungLaedt.set(false);
        void this.eRechnungFehlerAnzeigen(err);
      },
    });
  }

  /** Bei responseType 'blob' ist der 422-Fehlerkörper ein Blob — als Text lesen,
   * damit der Nutzer den echten Grund sieht (z. B. „Firmenprofil fehlt"). */
  private async eRechnungFehlerAnzeigen(err: unknown): Promise<void> {
    const koerper = (err as { error?: unknown })?.error;
    if (koerper instanceof Blob) {
      try {
        const detail = JSON.parse(await koerper.text())?.detail;
        if (typeof detail === 'string') {
          this.meldung.set({ art: 'fehler', text: detail });
          return;
        }
      } catch {
        /* kein JSON-Körper → generische Meldung unten */
      }
    }
    this.meldung.set({
      art: 'fehler',
      text:
        fehlerDetail(err) ??
        'Die E-Rechnung konnte nicht erzeugt werden. Bitte erneut versuchen.',
    });
  }
}
