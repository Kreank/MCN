import { Component, computed, effect, inject, input, output, signal } from '@angular/core';
import { FormArray, FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { map } from 'rxjs';
import { EigentumService } from '../../core/eigentum.service';
import { PartyService } from '../../core/party.service';
import {
  EIGENTUMSART_OPTIONEN,
  Eigentumsart,
  Eigentuemer,
  Eigentumsstand,
  QUELLENART_OPTIONEN,
  Quellenart,
  VOLLSTAENDIGKEIT_HINWEIS,
  VOLLSTAENDIGKEIT_OPTIONEN,
  Vollstaendigkeit,
} from '../../core/eigentum.model';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
import { apiFehlerZuweisen } from '../../shared/formular/api-fehler';
import {
  felderAlsBeruehrtMarkieren,
  serverFehlerZuruecksetzen,
} from '../../shared/formular/formular.util';

/** Was der Dialog gerade tut. Drei Wege, ein Formularrahmen. */
export type EigentumDialogModus =
  | { art: 'neu'; propertyId: string; unitId: string; wohnung: string }
  | {
      art: 'bearbeiten';
      propertyId: string;
      unitId: string;
      wohnung: string;
      stand: Eigentumsstand;
    }
  | {
      art: 'eigentuemer';
      propertyId: string;
      unitId: string;
      wohnung: string;
      stand: Eigentumsstand;
    }
  /**
   * Eine **bestehende** Beteiligung korrigieren.
   *
   * Ohne diesen Weg wäre der Normalfall eine Sackgasse: Man erfasst „teilweise
   * geklärt" mit unbestätigten Eigentümern (genau so ist es gedacht), bekommt
   * später die Eigentümerliste — und könnte den Stand nie auf „vollständig
   * geklärt" heben, weil der nur bestätigte Beteiligungen duldet und es keinen
   * Knopf zum Bestätigen gäbe. Dasselbe für einen vertippten Anteil.
   */
  | {
      art: 'beteiligung';
      propertyId: string;
      unitId: string;
      wohnung: string;
      stand: Eigentumsstand;
      person: Eigentuemer;
    };

/**
 * Eigentumsstand erfassen, ändern — oder einen weiteren Eigentümer ergänzen.
 *
 * **Der Eigentümer wird hier nicht angelegt, sondern ausgewählt.** Er ist ein
 * normaler Kontakt (`identity.party`) mit Adresse — und genau die Adresse ist
 * der Zweck: Er soll später als Rechnungsempfänger wählbar sein. Ihn hier
 * „schnell" anzulegen erzeugte Karteileichen ohne Anschrift.
 *
 * **Der Anteil ist ein Bruch, kein Prozentwert.** Zwei Felder, Zähler und
 * Nenner. Das ist unbequemer als ein Prozentfeld und trotzdem richtig: Drei
 * Erben zu je 1/3 lassen sich als Prozent nicht eingeben, ohne zu runden — und
 * gerundet wäre der Stand nie vollständig. Der Dialog rechnet die Summe live
 * mit und sagt, was noch fehlt.
 *
 * **Die Quelle ist Pflicht** (Beschluss A-14). Wer behauptet, wem etwas gehört,
 * muss sagen, woher er das hat.
 */
@Component({
  selector: 'app-eigentum-dialog',
  imports: [ReactiveFormsModule, Dialog, Feld, ReferenzWahl],
  templateUrl: './eigentum-dialog.html',
  styleUrl: './eigentum-dialog.scss',
})
export class EigentumDialog {
  private readonly fb = inject(FormBuilder);
  private readonly svc = inject(EigentumService);
  private readonly partySvc = inject(PartyService);

  readonly modus = input<EigentumDialogModus | null>(null);
  readonly fertig = output<boolean>();

  protected readonly laedt = signal(false);
  protected readonly formularFehler = signal<string | null>(null);

  protected readonly vollstaendigkeitOptionen = VOLLSTAENDIGKEIT_OPTIONEN;
  protected readonly quellenartOptionen = QUELLENART_OPTIONEN;
  protected readonly eigentumsartOptionen = EIGENTUMSART_OPTIONEN;
  protected readonly VOLLSTAENDIGKEIT_HINWEIS = VOLLSTAENDIGKEIT_HINWEIS;

  /** Kontaktsuche über den Server — ein Betrieb mit 800 Kontakten braucht sie. */
  protected readonly kontaktSuche: RefSuche = (q) =>
    this.partySvc
      .list({ page: 1, page_size: 20, q })
      .pipe(
        map((p) =>
          p.items.map((k) => ({ id: k.id, label: k.display_name, sub: k.party_type })),
        ),
      );

  protected readonly kopfForm = this.fb.group({
    distribution_status: this.fb.control<Vollstaendigkeit>('UNRESOLVED', {
      nonNullable: true,
    }),
    valid_from: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    valid_until: this.fb.control(''),
    source_type: this.fb.control<Quellenart>('OWNER_LIST', { nonNullable: true }),
    source_reference: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
  });

  /** Die Beteiligten beim Anlegen — ein Stand entsteht mit ihnen zusammen. */
  protected readonly beteiligte = this.fb.array<
    ReturnType<EigentumDialog['neueBeteiligung']>
  >([]);

  /** Formular für „einen Eigentümer ergänzen". */
  protected readonly ergaenzenForm = this.fb.group({
    party_id: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    share_numerator: this.fb.control<number | null>(null),
    share_denominator: this.fb.control<number | null>(null),
    ownership_type: this.fb.control<Eigentumsart>('CO_OWNER', { nonNullable: true }),
    bestaetigt: this.fb.control(false, { nonNullable: true }),
  });

  /**
   * Formular für „bestehende Beteiligung korrigieren".
   *
   * Ohne `party_id`: Der Eigentümer einer Beteiligung lässt sich nicht
   * austauschen — ein anderer Eigentümer ist eine andere Aussage, kein
   * korrigiertes Feld. Dafür gibt es den neuen Stand.
   */
  protected readonly beteiligungForm = this.fb.group({
    share_numerator: this.fb.control<number | null>(null),
    share_denominator: this.fb.control<number | null>(null),
    ownership_type: this.fb.control<Eigentumsart>('CO_OWNER', { nonNullable: true }),
    bestaetigt: this.fb.control(false, { nonNullable: true }),
  });

  protected readonly offen = computed(() => this.modus() !== null);

  protected readonly titel = computed(() => {
    const m = this.modus();
    if (!m) return '';
    if (m.art === 'neu') return `Eigentum erfassen — ${m.wohnung}`;
    if (m.art === 'bearbeiten') return `Eigentum bearbeiten — ${m.wohnung}`;
    if (m.art === 'eigentuemer') return `Eigentümer ergänzen — ${m.wohnung}`;
    return `${m.person.display_name} — Beteiligung korrigieren`;
  });

  /**
   * Die Anteilssumme als Bruch, live mitgerechnet.
   *
   * Der Server prüft dasselbe (und die Datenbank noch einmal) — aber erst beim
   * Speichern. Wer drei Erben einträgt, soll vorher sehen, dass 1/3 + 1/3 + 1/3
   * aufgeht und 333/1000 dreimal eben nicht.
   */
  protected readonly summe = computed(() => {
    this.formularTick();
    // `BigInt`, nicht `number`: Drei teilerfremde Nenner nahe der Obergrenze
    // 1.000.000 ergeben einen gemeinsamen Nenner von ~1e18 — mehr als
    // `Number.MAX_SAFE_INTEGER`. Die angezeigte Summe wäre dann stillschweigend
    // falsch, und eine Anzeige, die lügt, ist schlimmer als keine. Server
    // (`Fraction`) und Datenbank (`numeric`/LCM) rechnen ebenfalls exakt.
    let z = 0n;
    let n = 1n;
    let unvollstaendig = false;
    for (const gruppe of this.beteiligte.controls) {
      const zaehler = gruppe.controls.share_numerator.value;
      const nenner = gruppe.controls.share_denominator.value;
      if (!zaehler || !nenner) {
        unvollstaendig = true;
        continue;
      }
      // z/n + zaehler/nenner, mit Kürzen vor dem Multiplizieren.
      const bz = BigInt(zaehler);
      const bn = BigInt(nenner);
      const g = ggt(n, bn);
      z = z * (bn / g) + bz * (n / g);
      n = n * (bn / g);
      const t = ggt(z, n);
      z /= t;
      n /= t;
    }
    return {
      zaehler: z.toString(),
      nenner: n.toString(),
      unvollstaendig,
      vollstaendig: z === n && z > 0n,
    };
  });

  /** Zählt Formularänderungen, damit `summe` neu rechnet (FormArray ist kein Signal). */
  private readonly formularTick = signal(0);

  constructor() {
    this.beteiligte.valueChanges.subscribe(() => this.formularTick.update((v) => v + 1));

    effect(() => {
      const m = this.modus();
      if (!m) return;
      this.formularFehler.set(null);
      serverFehlerZuruecksetzen(this.kopfForm);

      if (m.art === 'neu') {
        this.kopfForm.reset({
          distribution_status: 'UNRESOLVED',
          valid_from: heuteIso(),
          valid_until: '',
          source_type: 'OWNER_LIST',
          source_reference: '',
        });
        this.beteiligte.clear();
      } else if (m.art === 'bearbeiten') {
        this.kopfForm.reset({
          distribution_status: m.stand.distribution_status,
          valid_from: m.stand.valid_from.slice(0, 10),
          valid_until: m.stand.valid_until?.slice(0, 10) ?? '',
          source_type: m.stand.source_type,
          source_reference: m.stand.source_reference,
        });
        this.beteiligte.clear();
      } else if (m.art === 'eigentuemer') {
        this.ergaenzenForm.reset({
          party_id: '',
          share_numerator: null,
          share_denominator: null,
          ownership_type: 'CO_OWNER',
          bestaetigt: false,
        });
      } else {
        // Korrektur einer bestehenden Beteiligung: Der Kontakt steht fest (er
        // lässt sich nicht austauschen), also nur Anteil, Art und Bestätigung.
        this.beteiligungForm.reset({
          share_numerator: m.person.share_numerator,
          share_denominator: m.person.share_denominator,
          ownership_type: m.person.ownership_type,
          bestaetigt: m.person.confirmation_status === 'CONFIRMED',
        });
      }
    });
  }

  protected neueBeteiligung() {
    return this.fb.group({
      party_id: this.fb.control('', {
        nonNullable: true,
        validators: [Validators.required],
      }),
      share_numerator: this.fb.control<number | null>(null),
      share_denominator: this.fb.control<number | null>(null),
      ownership_type: this.fb.control<Eigentumsart>('CO_OWNER', { nonNullable: true }),
      bestaetigt: this.fb.control(false, { nonNullable: true }),
    });
  }

  protected beteiligungHinzufuegen(): void {
    this.beteiligte.push(this.neueBeteiligung());
  }

  /**
   * Entfernt eine Zeile — **nur im Formular**, vor dem Speichern.
   *
   * Ein gespeicherter Eigentümer lässt sich nicht mehr entfernen (kein DELETE,
   * F-02); dafür wird der Stand beendet und neu angelegt. Hier ist noch nichts
   * gespeichert, also ist es eine reine Eingabekorrektur.
   */
  protected beteiligungEntfernen(index: number): void {
    this.beteiligte.removeAt(index);
  }

  protected schliessen(): void {
    if (!this.laedt()) this.fertig.emit(false);
  }

  protected speichern(): void {
    const m = this.modus();
    if (!m || this.laedt()) return;

    if (m.art === 'eigentuemer') {
      this.eigentuemerErgaenzen(m);
      return;
    }
    if (m.art === 'beteiligung') {
      this.beteiligungKorrigieren(m);
      return;
    }

    felderAlsBeruehrtMarkieren(this.kopfForm);
    if (this.kopfForm.invalid) return;
    for (const g of this.beteiligte.controls) {
      felderAlsBeruehrtMarkieren(g);
      if (g.invalid) return;
    }

    const v = this.kopfForm.getRawValue();
    this.laedt.set(true);
    this.formularFehler.set(null);

    if (m.art === 'bearbeiten') {
      this.svc
        .update(m.stand.id, {
          distribution_status: v.distribution_status,
          valid_from: v.valid_from,
          valid_until: v.valid_until || null,
          source_type: v.source_type,
          source_reference: v.source_reference,
        })
        .subscribe({
          next: () => this.erfolg(),
          error: (err) => this.misserfolg(err),
        });
      return;
    }

    this.svc
      .create(m.propertyId, {
        unit_id: m.unitId,
        valid_from: v.valid_from,
        valid_until: v.valid_until || null,
        source_type: v.source_type,
        source_reference: v.source_reference,
        distribution_status: v.distribution_status,
        eigentuemer: this.beteiligte.controls.map((g) => {
          const b = g.getRawValue();
          return {
            party_id: b.party_id,
            share_numerator: b.share_numerator,
            share_denominator: b.share_denominator,
            ownership_type: b.ownership_type,
            confirmation_status: b.bestaetigt
              ? ('CONFIRMED' as const)
              : ('UNCONFIRMED' as const),
          };
        }),
      })
      .subscribe({
        next: () => this.erfolg(),
        error: (err) => this.misserfolg(err),
      });
  }

  private eigentuemerErgaenzen(m: EigentumDialogModus & { art: 'eigentuemer' }): void {
    felderAlsBeruehrtMarkieren(this.ergaenzenForm);
    if (this.ergaenzenForm.invalid) return;
    const v = this.ergaenzenForm.getRawValue();
    this.laedt.set(true);
    this.formularFehler.set(null);
    this.svc
      .addEigentuemer(m.stand.id, {
        party_id: v.party_id,
        share_numerator: v.share_numerator,
        share_denominator: v.share_denominator,
        ownership_type: v.ownership_type,
        confirmation_status: v.bestaetigt ? 'CONFIRMED' : 'UNCONFIRMED',
      })
      .subscribe({
        next: () => this.erfolg(),
        error: (err) => this.misserfolg(err),
      });
  }

  private beteiligungKorrigieren(
    m: EigentumDialogModus & { art: 'beteiligung' },
  ): void {
    felderAlsBeruehrtMarkieren(this.beteiligungForm);
    if (this.beteiligungForm.invalid) return;
    const v = this.beteiligungForm.getRawValue();
    this.laedt.set(true);
    this.formularFehler.set(null);
    this.svc
      .updateEigentuemer(m.person.id, {
        share_numerator: v.share_numerator,
        share_denominator: v.share_denominator,
        ownership_type: v.ownership_type,
        confirmation_status: v.bestaetigt ? 'CONFIRMED' : 'UNCONFIRMED',
      })
      .subscribe({
        next: () => this.erfolg(),
        error: (err) => this.misserfolg(err),
      });
  }

  private erfolg(): void {
    this.laedt.set(false);
    this.fertig.emit(true);
  }

  private misserfolg(err: unknown): void {
    this.laedt.set(false);
    const m = this.modus();
    const form =
      m?.art === 'eigentuemer'
        ? this.ergaenzenForm
        : m?.art === 'beteiligung'
          ? this.beteiligungForm
          : this.kopfForm;
    this.formularFehler.set(apiFehlerZuweisen(err, form).formular);
  }
}

/** Größter gemeinsamer Teiler — kürzt die live gerechnete Anteilssumme. */
function ggt(a: bigint, b: bigint): bigint {
  let x = a < 0n ? -a : a;
  let y = b < 0n ? -b : b;
  while (y) {
    [x, y] = [y, x % y];
  }
  return x || 1n;
}

function heuteIso(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
