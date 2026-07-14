import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Mappe, MappeTab } from '../../shared/mappe/mappe';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { Dateien } from '../../shared/dateien/dateien';
import { ZielFilter } from '../../core/datei.model';
import { Dialog } from '../../shared/dialog/dialog';
import { Bestaetigung } from '../../shared/bestaetigung/bestaetigung';
import { Feld } from '../../shared/formular/feld';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';
import {
  apiZuDeAnzeige,
  apiZuDeEingabe,
  deZuApiDezimal,
  dezimalValidator,
} from '../../shared/formular/dezimal';
import { fristAbgelaufen, isoDatumDe } from '../../shared/datum';
import { VerbotenState, fehlerDetail, fehlerState, istVerboten } from '../../shared/http-fehler';
import { BuchhaltungService } from '../../core/buchhaltung.service';
import { BelegService } from '../../core/beleg.service';
import { MailService } from '../../core/mail.service';
import { AuthService } from '../../core/auth.service';
import { QuoteLine } from '../../core/beleg.model';
import {
  DunningNotice,
  OpenItemDetail,
  PAYMENT_TYPES,
  Payment,
  PaymentStatus,
  euro,
  invoiceTypeLabel,
  paymentStatusClass,
  paymentStatusLabel,
  paymentTypeLabel,
} from '../../core/buchhaltung.model';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: OpenItemDetail }
  | VerbotenState
  | { kind: 'error' };

// 'warten' = Vier-Augen-Antrag wurde angelegt, es wurde NOCH NICHTS ausgeführt.
type Meldung = { art: 'erfolg' | 'fehler' | 'warten'; text: string };

@Component({
  selector: 'app-buchhaltung-detail',
  imports: [
    Mappe,
    RouterLink,
    KeinZugriff,
    Dateien,
    Dialog,
    Bestaetigung,
    Feld,
    ReactiveFormsModule,
  ],
  templateUrl: './buchhaltung-detail.html',
  styleUrl: './buchhaltung-detail.scss',
})
export class BuchhaltungDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly svc = inject(BuchhaltungService);
  private readonly belege = inject(BelegService);
  private readonly mailSvc = inject(MailService);
  private readonly auth = inject(AuthService);
  private readonly fb = inject(FormBuilder);

  protected readonly tab = signal('uebersicht');
  protected readonly state = signal<ViewState>({ kind: 'loading' });
  private reqId = 0;

  protected readonly tabs: MappeTab[] = [
    { id: 'uebersicht', label: 'Übersicht' },
    { id: 'zahlungen', label: 'Zahlungen' },
    { id: 'mahnverlauf', label: 'Mahnverlauf' },
    { id: 'dateien', label: 'Dateien' },
  ];

  protected readonly paymentTypes = PAYMENT_TYPES;

  // --- Rechte --------------------------------------------------------------
  protected readonly darfZahlung = computed(() => this.auth.darf('invoicing', 'AENDERN'));
  protected readonly darfMahnung = computed(() => this.auth.darf('invoicing', 'VERSENDEN'));
  protected readonly darfStornieren = computed(() => this.auth.darf('invoicing', 'STORNIEREN'));

  protected readonly meldung = signal<Meldung | null>(null);

  /** Ein bereits vorhandener Stornobeleg schließt weiteres Stornieren aus. */
  protected readonly hatStorno = computed(() =>
    (this.daten()?.credit_notes ?? []).some((c) => c.invoice_type === 'STORNO'),
  );

  /**
   * Storno-/Gutschriftbelege sind selbst schon Folgebelege und lassen sich weder
   * erneut stornieren noch korrigieren (der Server lehnt es mit 422 ab — die UI
   * bietet es gar nicht erst an). Deckt sich mit `CREDIT_TYPES` im Backend.
   */
  protected readonly istFolgebeleg = computed(() => {
    const t = this.daten()?.invoice_type;
    return t === 'STORNO' || t === 'GUTSCHRIFT';
  });

  /** Diese Rechnung trägt (Teil-)Gutschriften — die Forderung ist gemindert. */
  protected readonly gutschriftsanteil = computed(
    () => !this.istFolgebeleg() && Number(this.daten()?.credit_total ?? 0) < 0,
  );

  /**
   * Der Kunde hat mehr gezahlt, als (noch) gefordert ist — der Betrag ist ihm zu
   * erstatten.
   *
   * **Auf einer Rechnung ist das nur noch die ECHTE Überzahlung** (er hat schlicht
   * zu viel überwiesen). Der Storno einer bezahlten Rechnung führt hier NICHT mehr
   * her: Seine Erstattungspflicht steht auf dem Kreditbeleg — und dort allein.
   * Vorher stand sie auf beiden Belegen und blieb am Original auch nach der
   * Erstattung stehen; wer die Liste abarbeitete, zahlte zweimal aus.
   */
  protected readonly guthaben = computed(
    () => !this.istFolgebeleg() && Number(this.daten()?.open_amount ?? 0) < 0,
  );

  // --- Verrechnung (Kreditbelege) -------------------------------------------
  /** Noch an den Kunden zurückzuzahlen (> 0 nur, solange etwas offen ist). */
  protected readonly zuErstatten = computed(() =>
    Number(this.daten()?.zu_erstatten ?? 0),
  );
  /** Bereits an den Kunden zurückgezahlt (Rückerstattungsbuchungen). */
  protected readonly bereitsErstattet = computed(() =>
    Number(this.daten()?.erstattet ?? 0),
  );
  /** Mit dem Gegenbeleg verrechneter Betrag (Rechnung ↔ Kreditbeleg). */
  protected readonly verrechnet = computed(() => Number(this.daten()?.verrechnet ?? 0));
  /** Was der Kunde auf DIESEN Beleg gezahlt hat. */
  protected readonly gezahlt = computed(() => Number(this.daten()?.paid_total ?? 0));

  /**
   * Der Teil der Storno-/Gutschriftbeträge, der NICHT mit der offenen Forderung
   * verrechnet werden konnte — also das Geld, das der Kunde bereits gezahlt hat und
   * das ihm zusteht. Es ist auf dem Kreditbeleg zu erstatten, nicht hier.
   */
  protected readonly unverrechneteGutschrift = computed(() => {
    const d = this.daten();
    if (!d || this.istFolgebeleg()) return 0;
    return -Number(d.credit_total) - this.verrechnet();
  });

  /**
   * Dieser Kreditbeleg ist (ganz oder teilweise) mit der offenen Forderung der
   * Ursprungsrechnung verrechnet — insoweit fließt kein Geld zurück.
   */
  protected readonly istVerrechnet = computed(
    () => this.istFolgebeleg() && this.verrechnet() > 0,
  );

  /** Der zu erstattende Betrag als deutsche Eingabe (ohne Tausenderpunkt!). */
  protected readonly zuErstattenEingabe = computed(() =>
    apiZuDeEingabe(this.daten()?.zu_erstatten ?? null, 2),
  );

  /** Kopfzeile: ein Kreditbeleg ist nicht „offen", er ist „zu erstatten". */
  protected readonly kopfBetrag = computed(() => {
    const d = this.daten();
    if (!d) return '';
    if (this.zuErstatten() > 0) return `${euro(d.zu_erstatten)} zu erstatten`;
    return `${euro(d.open_amount)} offen`;
  });

  // --- Zahlung erfassen ----------------------------------------------------
  protected readonly zahlungOffen = signal(false);
  protected readonly zahlungLaedt = signal(false);
  protected readonly zahlungMeldung = signal<string | null>(null);
  protected readonly zahlungForm = this.fb.group({
    amount: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, dezimalValidator],
    }),
    paid_at: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    payment_type: this.fb.control('ZAHLUNG', { nonNullable: true }),
    external_reference: this.fb.control('', { nonNullable: true }),
  });

  // --- Mahnung erzeugen ----------------------------------------------------
  protected readonly mahnungOffen = signal(false);
  protected readonly mahnungLaedt = signal(false);
  protected readonly mahnungMeldung = signal<string | null>(null);
  protected readonly mahnungForm = this.fb.group({
    issued_at: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    note: this.fb.control('', { nonNullable: true }),
  });
  /** Nächste (lückenlose) Mahnstufe = aktuelle + 1; die DB erzwingt max+1. */
  protected readonly naechsteStufe = computed(() => (this.daten()?.dunning_level ?? 0) + 1);

  // --- Mahnung per E-Mail senden -------------------------------------------
  /** Ob ein Absenderkonto hinterlegt ist (null = noch nicht geladen). Der Server
   *  bleibt maßgeblich; das UI blendet die Aktion ohne Konto nur aus. */
  protected readonly mailKontoVorhanden = signal<boolean | null>(null);
  /** Die Mahnung, die gerade versendet wird (steuert den Dialog). */
  protected readonly versandNotice = signal<DunningNotice | null>(null);
  protected readonly versandLaedt = signal(false);
  protected readonly versandMeldung = signal<string | null>(null);
  protected readonly versandForm = this.fb.group({
    to_address: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required, Validators.email],
    }),
  });

  // --- Storno (Rechnung) ---------------------------------------------------
  protected readonly stornoOffen = signal(false);
  protected readonly stornoLaedt = signal(false);

  // --- Zahlung stornieren --------------------------------------------------
  protected readonly zStornoZahlung = signal<Payment | null>(null);
  protected readonly zStornoLaedt = signal(false);

  // --- Rechnungskorrektur --------------------------------------------------
  protected readonly korrekturOffen = signal(false);
  protected readonly korrekturLaedt = signal(false);
  protected readonly korrekturMeldung = signal<string | null>(null);
  protected readonly korrekturLinesLaden = signal(false);
  protected readonly korrekturLines = signal<QuoteLine[]>([]);
  protected readonly korrekturAuswahl = signal<Set<number>>(new Set());
  /** Schlussrechnung mit Anrechnung: nur Vollstorno zulässig (Server erzwingt es). */
  protected readonly korrekturNurStorno = signal(false);

  private readonly dateFmt = new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });

  protected readonly daten = computed(() => {
    const s = this.state();
    return s.kind === 'ready' ? s.data : null;
  });

  /** Der offene Posten IST die Rechnung — `id` ist die invoice_id. */
  protected readonly dateienZiel = computed<ZielFilter>(() => ({
    invoice_id: this.daten()?.id ?? '',
  }));

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((pm) => {
      const id = pm.get('id');
      this.tab.set('uebersicht');
      if (!id) {
        this.state.set({ kind: 'error' });
        return;
      }
      this.load(id);
    });

    // Ob ein Absenderkonto konfiguriert ist, entscheidet über die Versand-Aktion
    // im Mahnverlauf. Nur laden, wenn die Rolle überhaupt versenden darf.
    if (this.darfMahnung()) {
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
    this.svc.getOpenItem(id).subscribe({
      next: (data) => {
        if (rid === this.reqId) this.state.set({ kind: 'ready', data });
      },
      error: (err) => {
        if (rid === this.reqId) this.state.set(fehlerState(err));
      },
    });
  }

  private neuLaden(): void {
    const id = this.daten()?.id ?? this.route.snapshot.paramMap.get('id');
    if (id) this.load(id);
  }

  meldungSchliessen(): void {
    this.meldung.set(null);
  }

  /** Dekoratives Zeichen (aria-hidden) je Meldungsart. */
  meldeMark(art: Meldung['art']): string {
    if (art === 'fehler') return '!';
    if (art === 'warten') return '⋯';
    return '✓';
  }

  /** Wortmarke — der eigentliche Statusträger (WCAG: nie nur Farbe). */
  meldeWort(art: Meldung['art']): string {
    if (art === 'fehler') return 'Fehler:';
    if (art === 'warten') return 'Freigabe angefordert:';
    return 'Erledigt:';
  }

  private aktionsFehler(err: unknown): string {
    if (istVerboten(err)) return fehlerDetail(err) ?? 'Keine Berechtigung für diese Aktion.';
    return fehlerDetail(err) ?? 'Die Aktion ist fehlgeschlagen. Bitte erneut versuchen.';
  }

  // ---- Zahlung erfassen ---------------------------------------------------
  zahlungOeffnen(): void {
    this.zahlungForm.reset({
      // Auf einem Kreditbeleg ist die zu erfassende Buchung die RÜCKERSTATTUNG (dort
      // und nur dort wird sie gebucht) — und ihr Betrag steht fest: der offene Rest.
      // Vorbelegen, nicht raten lassen: Ein hier erfasster „Zahlungseingang" wäre die
      // Umkehrung des Vorgangs.
      amount: this.zuErstatten() > 0 ? this.zuErstattenEingabe() : '',
      paid_at: this.heute(),
      payment_type: this.istFolgebeleg() ? 'RUECKERSTATTUNG' : 'ZAHLUNG',
      external_reference: '',
    });
    this.zahlungMeldung.set(null);
    this.meldung.set(null);
    this.zahlungOffen.set(true);
  }

  zahlungSchliessen(): void {
    if (!this.zahlungLaedt()) this.zahlungOffen.set(false);
  }

  zahlungAbsenden(): void {
    const d = this.daten();
    if (!d || this.zahlungLaedt()) return;
    serverFehlerZuruecksetzen(this.zahlungForm);
    this.zahlungMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.zahlungForm);
    if (this.zahlungForm.invalid) return;

    const v = this.zahlungForm.getRawValue();
    this.zahlungLaedt.set(true);
    this.svc
      .recordPayment(d.id, {
        amount: deZuApiDezimal(v.amount),
        paid_at: v.paid_at,
        payment_type: v.payment_type,
        external_reference: v.external_reference.trim() || null,
      })
      .subscribe({
        next: () => {
          this.zahlungLaedt.set(false);
          this.zahlungOffen.set(false);
          this.meldung.set({ art: 'erfolg', text: 'Zahlung wurde erfasst.' });
          this.neuLaden();
        },
        error: (err) => {
          this.zahlungLaedt.set(false);
          this.zahlungMeldung.set(apiFehlerZuweisen(err, this.zahlungForm).formular);
        },
      });
  }

  // ---- Mahnung erzeugen ---------------------------------------------------
  mahnungOeffnen(): void {
    this.mahnungForm.reset({ issued_at: this.heute(), note: '' });
    this.mahnungMeldung.set(null);
    this.meldung.set(null);
    this.mahnungOffen.set(true);
  }

  mahnungSchliessen(): void {
    if (!this.mahnungLaedt()) this.mahnungOffen.set(false);
  }

  mahnungAbsenden(): void {
    const d = this.daten();
    if (!d || this.mahnungLaedt()) return;
    serverFehlerZuruecksetzen(this.mahnungForm);
    this.mahnungMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.mahnungForm);
    if (this.mahnungForm.invalid) return;

    const v = this.mahnungForm.getRawValue();
    this.mahnungLaedt.set(true);
    this.svc
      .issueDunning(d.id, {
        level: this.naechsteStufe(),
        issued_at: v.issued_at,
        note: v.note.trim() || null,
      })
      .subscribe({
        next: (notice) => {
          this.mahnungLaedt.set(false);
          this.mahnungOffen.set(false);
          this.meldung.set({
            art: 'erfolg',
            text: `Mahnung erzeugt: ${notice.level}. Stufe (${notice.label}).`,
          });
          this.neuLaden();
        },
        error: (err) => {
          this.mahnungLaedt.set(false);
          this.mahnungMeldung.set(apiFehlerZuweisen(err, this.mahnungForm).formular);
        },
      });
  }

  // ---- Mahnung per E-Mail senden ------------------------------------------
  /** Ob die Versand-Aktion je Mahnung angeboten wird (Recht + Absenderkonto). */
  protected readonly kannVersenden = computed(
    () => this.darfMahnung() && this.mailKontoVorhanden() === true,
  );

  versandOeffnen(notice: DunningNotice): void {
    if (!this.kannVersenden()) return;
    this.versandForm.reset({ to_address: this.daten()?.recipient_email ?? '' });
    serverFehlerZuruecksetzen(this.versandForm);
    this.versandMeldung.set(null);
    this.meldung.set(null);
    this.versandNotice.set(notice);
  }

  versandSchliessen(): void {
    if (!this.versandLaedt()) this.versandNotice.set(null);
  }

  versandAbsenden(): void {
    const notice = this.versandNotice();
    if (!notice || this.versandLaedt()) return;
    serverFehlerZuruecksetzen(this.versandForm);
    this.versandMeldung.set(null);
    felderAlsBeruehrtMarkieren(this.versandForm);
    if (this.versandForm.invalid) return;

    const to = this.versandForm.controls.to_address.value.trim();
    this.versandLaedt.set(true);
    this.svc.sendDunningEmail(notice.id, to).subscribe({
      next: (res) => {
        this.versandLaedt.set(false);
        this.versandNotice.set(null);
        this.meldung.set({
          art: 'erfolg',
          text: `${notice.label} wurde als E-Mail (Rechnung als PDF) an ${res.to_address} gesendet.`,
        });
      },
      error: (err) => {
        this.versandLaedt.set(false);
        this.versandMeldung.set(apiFehlerZuweisen(err, this.versandForm).formular);
      },
    });
  }

  // ---- Storno -------------------------------------------------------------
  stornoFragen(): void {
    this.meldung.set(null);
    this.stornoOffen.set(true);
  }

  stornoAbbrechen(): void {
    if (!this.stornoLaedt()) this.stornoOffen.set(false);
  }

  stornoBestaetigen(): void {
    const d = this.daten();
    if (!d || this.stornoLaedt()) return;
    this.stornoLaedt.set(true);
    this.svc.cancelInvoice(d.id).subscribe({
      next: (ergebnis) => {
        this.stornoLaedt.set(false);
        this.stornoOffen.set(false);
        if (ergebnis.kind === 'wartet') {
          // 202: Es wurde NUR ein Freigabeantrag angelegt — nicht storniert.
          this.meldung.set({ art: 'warten', text: ergebnis.pending.detail });
          return;
        }
        this.meldung.set({
          art: 'erfolg',
          text: `Stornobeleg ${ergebnis.credit.invoice_number ?? '—'} wurde erzeugt.`,
        });
        this.neuLaden();
      },
      error: (err) => {
        this.stornoLaedt.set(false);
        this.stornoOffen.set(false);
        this.meldung.set({ art: 'fehler', text: this.aktionsFehler(err) });
      },
    });
  }

  // ---- Zahlung stornieren -------------------------------------------------
  zStornoFragen(p: Payment): void {
    if (!p.is_reversible) return;
    this.meldung.set(null);
    this.zStornoZahlung.set(p);
  }

  zStornoAbbrechen(): void {
    if (!this.zStornoLaedt()) this.zStornoZahlung.set(null);
  }

  zStornoBestaetigen(): void {
    const p = this.zStornoZahlung();
    if (!p || this.zStornoLaedt()) return;
    this.zStornoLaedt.set(true);
    this.svc.reversePayment(p.id).subscribe({
      next: () => {
        this.zStornoLaedt.set(false);
        this.zStornoZahlung.set(null);
        this.meldung.set({ art: 'erfolg', text: 'Die Zahlung wurde durch eine Gegenbuchung storniert.' });
        this.neuLaden();
      },
      error: (err) => {
        this.zStornoLaedt.set(false);
        this.zStornoZahlung.set(null);
        this.meldung.set({ art: 'fehler', text: this.aktionsFehler(err) });
      },
    });
  }

  // ---- Rechnungskorrektur -------------------------------------------------
  korrekturOeffnen(): void {
    const d = this.daten();
    if (!d) return;
    this.korrekturMeldung.set(null);
    this.meldung.set(null);
    this.korrekturAuswahl.set(new Set());
    this.korrekturLines.set([]);
    this.korrekturNurStorno.set(false);
    this.korrekturOffen.set(true);
    // Positionen liegen nicht im offenen Posten — aus dem Rechnungsbeleg holen.
    this.korrekturLinesLaden.set(true);
    this.belege.getInvoice(d.id).subscribe({
      next: (inv) => {
        this.korrekturLinesLaden.set(false);
        // Anrechnungspositionen einer Schlussrechnung sind NICHT korrigierbar:
        // sie sind negative Abzüge. Eine „Gutschrift" darauf dreht das Vorzeichen
        // um und fordert den Abschlag ein zweites Mal. Der Server lehnt das ab
        // (422); hier stehen sie gar nicht erst zur Wahl. Eine Schlussrechnung mit
        // Anrechnung lässt sich überhaupt nur vollständig stornieren.
        this.korrekturLines.set(inv.lines.filter((l) => !l.advance_invoice_id));
        this.korrekturNurStorno.set(inv.advances.length > 0);
      },
      error: () => {
        this.korrekturLinesLaden.set(false);
        this.korrekturMeldung.set('Die Positionen konnten nicht geladen werden.');
      },
    });
  }

  korrekturSchliessen(): void {
    if (!this.korrekturLaedt()) this.korrekturOffen.set(false);
  }

  korrekturUmschalten(position: number, checked: boolean): void {
    const next = new Set(this.korrekturAuswahl());
    if (checked) next.add(position);
    else next.delete(position);
    this.korrekturAuswahl.set(next);
  }

  korrekturAbsenden(): void {
    const d = this.daten();
    if (!d || this.korrekturLaedt()) return;
    const positions = [...this.korrekturAuswahl()].sort((a, b) => a - b);
    if (positions.length === 0) {
      this.korrekturMeldung.set('Bitte mindestens eine Position wählen.');
      return;
    }
    this.korrekturMeldung.set(null);
    this.korrekturLaedt.set(true);
    this.svc.correctInvoice(d.id, { positions }).subscribe({
      next: (ergebnis) => {
        this.korrekturLaedt.set(false);
        this.korrekturOffen.set(false);
        if (ergebnis.kind === 'wartet') {
          // 202: Nur ein Freigabeantrag angelegt — es wurde nichts gutgeschrieben.
          this.meldung.set({ art: 'warten', text: ergebnis.pending.detail });
          return;
        }
        this.meldung.set({
          art: 'erfolg',
          text: `Rechnungskorrektur ${ergebnis.credit.invoice_number ?? '—'} (Gutschrift) wurde erzeugt.`,
        });
        this.neuLaden();
      },
      error: (err) => {
        this.korrekturLaedt.set(false);
        this.korrekturMeldung.set(this.aktionsFehler(err));
      },
    });
  }

  private heute(): string {
    return new Date().toISOString().slice(0, 10);
  }

  // ---- Darstellungshelfer -------------------------------------------------
  statusLabel(s: PaymentStatus): string {
    return paymentStatusLabel(s);
  }
  statusClass(s: PaymentStatus): string {
    return paymentStatusClass(s);
  }
  typeLabel(t: string): string {
    return invoiceTypeLabel(t);
  }
  paymentTypeLabel(t: string): string {
    return paymentTypeLabel(t);
  }
  euro(v: string | null): string {
    return euro(v);
  }
  /** Wie `euro`, aber für bereits gerechnete Zahlen (verrechnete Restbeträge). */
  euroZahl(v: number): string {
    return euro(v.toFixed(2));
  }
  d(iso: string | null): string {
    if (!iso) return '—';
    // Reine Datumswerte nicht durch `new Date` schicken: die parst sie als
    // UTC-Mitternacht und zeigt sie bei negativem Client-Offset einen Tag zu frueh.
    if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) return isoDatumDe(iso);
    return this.dateFmt.format(new Date(iso));
  }

  /** Skonto im Klartext (nie nur eine Zahl). Alles vom Server gerechnet.
   * Eine abgelaufene Frist wird ausdrücklich benannt — sonst läse sich ein Monate
   * alter offener Posten wie ein noch einlösbarer Skontoabzug. */
  protected skontoText(): string {
    const x = this.daten();
    if (!x?.skonto_betrag || !x.skonto_bis) return '—';
    const satz = apiZuDeAnzeige(x.discount_percent, 2);
    const kern =
      `${satz} % bis ${this.d(x.skonto_bis)} — ${euro(x.skonto_betrag)} Abzug, ` +
      `zahlbar ${euro(x.skonto_zahlbetrag)}`;
    return fristAbgelaufen(x.skonto_bis) ? `${kern} (Frist abgelaufen)` : kern;
  }
}
