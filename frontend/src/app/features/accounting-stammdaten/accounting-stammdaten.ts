import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { forkJoin } from 'rxjs';
import { BelegerfassungService } from '../../core/belegerfassung.service';
import { AuthService } from '../../core/auth.service';
import {
  CostCenter,
  LedgerAccount,
  accountTypeLabel,
} from '../../core/belegerfassung.model';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';

type Bereich = 'konten' | 'kostenstellen';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready' }
  | VerbotenState
  | { kind: 'error' };

/**
 * Accounting-Stammdaten: Buchungskonten und Kostenstellen (Schema `accounting`).
 * Anlegen, Bezeichnung ändern, Deaktivieren (kein Löschen — alte Belege behalten
 * ihre Kontierung). Anlegen erfordert `accounting/ANLEGEN`, Ändern/Deaktivieren
 * `accounting/AENDERN`; ohne diese Rechte ist die Ansicht schreibgeschützt.
 */
@Component({
  selector: 'app-accounting-stammdaten',
  imports: [RouterLink, ReactiveFormsModule, Feld, KeinZugriff],
  templateUrl: './accounting-stammdaten.html',
  styleUrl: './accounting-stammdaten.scss',
})
export class AccountingStammdaten {
  private readonly svc = inject(BelegerfassungService);
  private readonly fb = inject(FormBuilder);
  private readonly auth = inject(AuthService);

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly bereich = signal<Bereich>('konten');

  protected readonly darfAnlegen = computed(() => this.auth.darf('accounting', 'ANLEGEN'));
  protected readonly darfAendern = computed(() => this.auth.darf('accounting', 'AENDERN'));

  protected readonly ledgers = signal<LedgerAccount[]>([]);
  protected readonly costCenters = signal<CostCenter[]>([]);

  protected readonly laedt = signal(false);
  protected readonly meldung = signal<string | null>(null);
  /** Aktuell inline bearbeitete Zeile (Präfix 'l:' Konto / 'c:' Kostenstelle). */
  protected readonly bearbeite = signal<string | null>(null);

  protected readonly accountTypeOptionen: FeldOption[] = [
    { wert: 'AKTIV', label: 'Aktivkonto' },
    { wert: 'PASSIV', label: 'Passivkonto' },
    { wert: 'AUFWAND', label: 'Aufwandskonto' },
    { wert: 'ERTRAG', label: 'Ertragskonto' },
  ];
  protected readonly chartOptionen: FeldOption[] = [
    { wert: 'SKR03', label: 'SKR03' },
    { wert: 'SKR04', label: 'SKR04' },
  ];

  protected readonly ledgerForm = this.fb.group({
    account_number: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    label: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    account_type: this.fb.control('AUFWAND', { nonNullable: true, validators: [Validators.required] }),
    chart_of_accounts: this.fb.control('', { nonNullable: true }),
    notes: this.fb.control('', { nonNullable: true }),
  });

  protected readonly costCenterForm = this.fb.group({
    code: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    label: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    notes: this.fb.control('', { nonNullable: true }),
  });

  protected readonly editLabel = this.fb.control('', {
    nonNullable: true,
    validators: [Validators.required],
  });

  constructor() {
    this.laden();
  }

  private laden(): void {
    this.state.set({ kind: 'loading' });
    forkJoin({
      ledgers: this.svc.listLedgerAccounts(true),
      costCenters: this.svc.listCostCenters(true),
    }).subscribe({
      next: ({ ledgers, costCenters }) => {
        this.ledgers.set(ledgers);
        this.costCenters.set(costCenters);
        this.state.set({ kind: 'ready' });
      },
      error: (err: unknown) => this.state.set(fehlerState(err)),
    });
  }

  waehleBereich(b: Bereich): void {
    if (this.bereich() === b) return;
    this.bereich.set(b);
    this.bearbeite.set(null);
    this.meldung.set(null);
  }

  // ---- Buchungskonten -----------------------------------------------------
  private neuLedgerLaden(): void {
    this.svc.listLedgerAccounts(true).subscribe({
      next: (a) => this.ledgers.set(a),
      error: () => {},
    });
  }

  ledgerAnlegen(): void {
    if (this.laedt() || !this.darfAnlegen()) return;
    this.meldung.set(null);
    this.ledgerForm.markAllAsTouched();
    if (this.ledgerForm.invalid) return;
    const v = this.ledgerForm.getRawValue();
    this.laedt.set(true);
    this.svc
      .createLedgerAccount({
        account_number: v.account_number.trim(),
        label: v.label.trim(),
        account_type: v.account_type as LedgerAccount['account_type'],
        chart_of_accounts: v.chart_of_accounts || null,
        notes: v.notes.trim() || null,
      })
      .subscribe({
        next: () => {
          this.laedt.set(false);
          this.ledgerForm.reset({
            account_number: '',
            label: '',
            account_type: 'AUFWAND',
            chart_of_accounts: '',
            notes: '',
          });
          this.neuLedgerLaden();
        },
        error: (err: unknown) => {
          this.laedt.set(false);
          this.meldung.set(fehlerDetail(err) ?? 'Das Buchungskonto konnte nicht angelegt werden.');
        },
      });
  }

  starteLedger(a: LedgerAccount): void {
    if (!this.darfAendern()) return;
    this.meldung.set(null);
    this.editLabel.setValue(a.label);
    this.bearbeite.set(`l:${a.id}`);
  }

  speichereLedger(a: LedgerAccount): void {
    if (this.laedt()) return;
    this.editLabel.markAsTouched();
    if (this.editLabel.invalid) return;
    this.laedt.set(true);
    this.svc.updateLedgerAccount(a.id, { label: this.editLabel.value.trim() }).subscribe({
      next: () => {
        this.laedt.set(false);
        this.bearbeite.set(null);
        this.neuLedgerLaden();
      },
      error: (err: unknown) => {
        this.laedt.set(false);
        this.meldung.set(fehlerDetail(err) ?? 'Das Buchungskonto konnte nicht gespeichert werden.');
      },
    });
  }

  umschaltenLedger(a: LedgerAccount): void {
    if (this.laedt() || !this.darfAendern()) return;
    this.meldung.set(null);
    this.laedt.set(true);
    this.svc.updateLedgerAccount(a.id, { active: !a.active }).subscribe({
      next: () => {
        this.laedt.set(false);
        this.neuLedgerLaden();
      },
      error: (err: unknown) => {
        this.laedt.set(false);
        this.meldung.set(fehlerDetail(err) ?? 'Der Status konnte nicht geändert werden.');
      },
    });
  }

  // ---- Kostenstellen ------------------------------------------------------
  private neuCcLaden(): void {
    this.svc.listCostCenters(true).subscribe({
      next: (c) => this.costCenters.set(c),
      error: () => {},
    });
  }

  ccAnlegen(): void {
    if (this.laedt() || !this.darfAnlegen()) return;
    this.meldung.set(null);
    this.costCenterForm.markAllAsTouched();
    if (this.costCenterForm.invalid) return;
    const v = this.costCenterForm.getRawValue();
    this.laedt.set(true);
    this.svc
      .createCostCenter({
        code: v.code.trim(),
        label: v.label.trim(),
        notes: v.notes.trim() || null,
      })
      .subscribe({
        next: () => {
          this.laedt.set(false);
          this.costCenterForm.reset({ code: '', label: '', notes: '' });
          this.neuCcLaden();
        },
        error: (err: unknown) => {
          this.laedt.set(false);
          this.meldung.set(fehlerDetail(err) ?? 'Die Kostenstelle konnte nicht angelegt werden.');
        },
      });
  }

  starteCc(c: CostCenter): void {
    if (!this.darfAendern()) return;
    this.meldung.set(null);
    this.editLabel.setValue(c.label);
    this.bearbeite.set(`c:${c.id}`);
  }

  speichereCc(c: CostCenter): void {
    if (this.laedt()) return;
    this.editLabel.markAsTouched();
    if (this.editLabel.invalid) return;
    this.laedt.set(true);
    this.svc.updateCostCenter(c.id, { label: this.editLabel.value.trim() }).subscribe({
      next: () => {
        this.laedt.set(false);
        this.bearbeite.set(null);
        this.neuCcLaden();
      },
      error: (err: unknown) => {
        this.laedt.set(false);
        this.meldung.set(fehlerDetail(err) ?? 'Die Kostenstelle konnte nicht gespeichert werden.');
      },
    });
  }

  umschaltenCc(c: CostCenter): void {
    if (this.laedt() || !this.darfAendern()) return;
    this.meldung.set(null);
    this.laedt.set(true);
    this.svc.updateCostCenter(c.id, { active: !c.active }).subscribe({
      next: () => {
        this.laedt.set(false);
        this.neuCcLaden();
      },
      error: (err: unknown) => {
        this.laedt.set(false);
        this.meldung.set(fehlerDetail(err) ?? 'Der Status konnte nicht geändert werden.');
      },
    });
  }

  abbrechen(): void {
    this.bearbeite.set(null);
  }

  // ---- Darstellungshelfer -------------------------------------------------
  typLabel(t: string): string {
    return accountTypeLabel(t);
  }
}
