import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { map } from 'rxjs';
import { AnbindungService } from '../../core/anbindung.service';
import {
  CredentialStatus,
  SupplierConnection,
  kindLabel,
  sourceSystemLabel,
  statusLabel,
} from '../../core/anbindung.model';
import { AuthService } from '../../core/auth.service';
import { PartyService } from '../../core/party.service';
import { Dialog } from '../../shared/dialog/dialog';
import { Feld, FeldOption } from '../../shared/formular/feld';
import { ReferenzWahl, RefSuche } from '../../shared/formular/referenz-wahl';
import { KeinZugriff } from '../../shared/kein-zugriff/kein-zugriff';
import { DatanormImport } from './datanorm-import/datanorm-import';
import { VerbotenState, fehlerDetail, fehlerState } from '../../shared/http-fehler';

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready' }
  | VerbotenState
  | { kind: 'error' };

/**
 * Lieferanten-Anbindungen (pricing.supplier_connection): Registry der
 * Katalog-Anbindungen (DATANORM / IDS-Connect). Anlegen, Bezeichnung/Shop/Status
 * ändern, Deaktivieren — kein Löschen (am Namespace hängen Artikelreferenzen).
 * Quellsystem, Namespace und Lieferant sind nach dem Anlegen unveränderlich.
 *
 * `credential_reference` ist ein VERWEIS auf den Secret-Store, nie das Secret
 * selbst (der eigentliche IDS-Connect-Warenkorb-Roundtrip ist ein späterer Slice).
 * Anlegen erfordert `pricing/ANLEGEN`, Ändern `pricing/AENDERN`.
 */
@Component({
  selector: 'app-haendler-anbindungen',
  imports: [
    RouterLink,
    ReactiveFormsModule,
    Dialog,
    Feld,
    ReferenzWahl,
    KeinZugriff,
    DatanormImport,
  ],
  templateUrl: './haendler-anbindungen.html',
  styleUrl: './haendler-anbindungen.scss',
})
export class HaendlerAnbindungen {
  private readonly svc = inject(AnbindungService);
  private readonly fb = inject(FormBuilder);
  private readonly auth = inject(AuthService);
  private readonly partySvc = inject(PartyService);

  protected readonly state = signal<ViewState>({ kind: 'loading' });
  protected readonly connections = signal<SupplierConnection[]>([]);

  protected readonly darfAnlegen = computed(() => this.auth.darf('pricing', 'ANLEGEN'));
  protected readonly darfAendern = computed(() => this.auth.darf('pricing', 'AENDERN'));

  protected readonly dialogOffen = signal(false);
  protected readonly dialogBusy = signal(false);
  protected readonly dialogFehler = signal<string | null>(null);
  /** Fehlermeldung für Listen-Aktionen (Status umschalten) außerhalb des Dialogs. */
  protected readonly listenFehler = signal<string | null>(null);
  /** null = Anlegen, sonst die bearbeitete Anbindung (Identität read-only). */
  protected readonly bearbeite = signal<SupplierConnection | null>(null);

  protected readonly systemOptionen: FeldOption[] = [
    { wert: 'IDS_CONNECT', label: 'IDS-Connect' },
    { wert: 'DATANORM', label: 'DATANORM' },
  ];
  protected readonly kindOptionen: FeldOption[] = [
    { wert: 'GROSSHAENDLER', label: 'Großhändler' },
    { wert: 'HERSTELLER', label: 'Hersteller' },
  ];

  /** Lieferantensuche über den Kontaktstamm (identity.party). */
  protected readonly lieferantSuche: RefSuche = (q) =>
    this.partySvc
      .list({ page: 1, page_size: 20, q })
      .pipe(map((p) => p.items.map((o) => ({ id: o.id, label: o.display_name }))));

  // --- DATANORM-Import -----------------------------------------------------
  protected readonly importOffen = signal(false);
  protected readonly importConn = signal<SupplierConnection | null>(null);

  datanormImportOeffnen(c: SupplierConnection): void {
    if (!this.darfAnlegen()) return;
    this.importConn.set(c);
    this.importOffen.set(true);
  }

  /** Nach einem echten Import: Liste neu laden (zeigt last_import_at aktuell). */
  neuLadenNachImport(): void {
    this.neuLaden();
  }

  // --- Zugangsdaten (IDS-Connect) ------------------------------------------
  protected readonly credOffen = signal(false);
  protected readonly credBusy = signal(false);
  protected readonly credFehler = signal<string | null>(null);
  protected readonly credConn = signal<SupplierConnection | null>(null);
  protected readonly credStatus = signal<CredentialStatus | null>(null);

  protected readonly credForm = this.fb.group({
    username: this.fb.control('', { nonNullable: true }),
    customer_number: this.fb.control('', { nonNullable: true }),
    password: this.fb.control('', { nonNullable: true }),
    passwort_entfernen: this.fb.control(false, { nonNullable: true }),
  });

  protected readonly form = this.fb.group({
    supplier_party_id: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    source_namespace: this.fb.control('', {
      nonNullable: true,
      validators: [Validators.required],
    }),
    label: this.fb.control('', { nonNullable: true, validators: [Validators.required] }),
    source_system: this.fb.control('IDS_CONNECT', { nonNullable: true }),
    connection_kind: this.fb.control('GROSSHAENDLER', { nonNullable: true }),
    shop_url: this.fb.control('', { nonNullable: true }),
    credential_reference: this.fb.control('', { nonNullable: true }),
  });

  constructor() {
    this.laden();
  }

  private laden(): void {
    this.state.set({ kind: 'loading' });
    this.svc.list(true).subscribe({
      next: (c) => {
        this.connections.set(c);
        this.state.set({ kind: 'ready' });
      },
      error: (err: unknown) => this.state.set(fehlerState(err)),
    });
  }

  private neuLaden(): void {
    this.svc.list(true).subscribe({ next: (c) => this.connections.set(c), error: () => {} });
  }

  neu(): void {
    if (!this.darfAnlegen()) return;
    this.bearbeite.set(null);
    this.dialogFehler.set(null);
    this.form.reset({
      supplier_party_id: '',
      source_namespace: '',
      label: '',
      source_system: 'IDS_CONNECT',
      connection_kind: 'GROSSHAENDLER',
      shop_url: '',
      credential_reference: '',
    });
    this.dialogOffen.set(true);
  }

  bearbeiten(c: SupplierConnection): void {
    if (!this.darfAendern()) return;
    this.bearbeite.set(c);
    this.dialogFehler.set(null);
    // Identität (Lieferant/Namespace/System) ist unveränderlich → nur die
    // pflegbaren Felder ins Formular.
    this.form.reset({
      supplier_party_id: c.supplier_party_id,
      source_namespace: c.source_namespace,
      label: c.label,
      source_system: c.source_system,
      connection_kind: c.connection_kind,
      shop_url: c.shop_url ?? '',
      credential_reference: c.credential_reference ?? '',
    });
    this.dialogOffen.set(true);
  }

  schliessen(): void {
    if (this.dialogBusy()) return;
    this.dialogOffen.set(false);
  }

  speichern(): void {
    if (this.dialogBusy()) return;
    const editing = this.bearbeite();
    // Beim Anlegen sind Lieferant + Namespace Pflicht; beim Bearbeiten nicht relevant.
    if (!editing) {
      this.form.markAllAsTouched();
      if (this.form.invalid) return;
    } else {
      this.form.controls.label.markAsTouched();
      if (this.form.controls.label.invalid) return;
    }
    const v = this.form.getRawValue();
    this.dialogBusy.set(true);
    this.dialogFehler.set(null);

    const fertig = () => {
      this.dialogBusy.set(false);
      this.dialogOffen.set(false);
      this.neuLaden();
    };
    const fehler = (err: unknown, fallback: string) => {
      this.dialogBusy.set(false);
      this.dialogFehler.set(fehlerDetail(err) ?? fallback);
    };

    if (editing) {
      this.svc
        .update(editing.id, {
          label: v.label.trim(),
          connection_kind: v.connection_kind,
          shop_url: v.shop_url.trim() || null,
          credential_reference: v.credential_reference.trim() || null,
        })
        .subscribe({
          next: fertig,
          error: (err) => fehler(err, 'Die Anbindung konnte nicht gespeichert werden.'),
        });
    } else {
      this.svc
        .create({
          supplier_party_id: v.supplier_party_id,
          source_namespace: v.source_namespace.trim(),
          label: v.label.trim(),
          source_system: v.source_system,
          connection_kind: v.connection_kind,
          shop_url: v.shop_url.trim() || null,
          credential_reference: v.credential_reference.trim() || null,
        })
        .subscribe({
          next: fertig,
          error: (err) => fehler(err, 'Die Anbindung konnte nicht angelegt werden.'),
        });
    }
  }

  // ---- Zugangsdaten (Benutzername/Kundennummer/Passwort) -------------------
  zugangsdaten(c: SupplierConnection): void {
    if (!this.darfAendern()) return;
    this.credConn.set(c);
    this.credStatus.set(null);
    this.credFehler.set(null);
    this.credForm.reset({
      username: '',
      customer_number: '',
      password: '',
      passwort_entfernen: false,
    });
    this.credOffen.set(true);
    // Aktuellen Status laden (nie das Passwort) und die Felder vorbelegen.
    this.svc.credentials(c.id).subscribe({
      next: (s) => {
        this.credStatus.set(s);
        this.credForm.patchValue({
          username: s.username ?? '',
          customer_number: s.customer_number ?? '',
        });
      },
      error: (err: unknown) =>
        this.credFehler.set(fehlerDetail(err) ?? 'Zugangsdaten konnten nicht geladen werden.'),
    });
  }

  zugangsdatenSchliessen(): void {
    if (this.credBusy()) return;
    this.credOffen.set(false);
  }

  zugangsdatenSpeichern(): void {
    const c = this.credConn();
    if (!c || this.credBusy()) return;
    const v = this.credForm.getRawValue();
    // Passwort write-only: entfernen = "", neu = eingegebener Wert, sonst
    // weglassen (unverändert). username/customer_number immer mitsenden.
    const payload: {
      username: string | null;
      customer_number: string | null;
      password?: string | null;
    } = {
      username: v.username.trim() || null,
      customer_number: v.customer_number.trim() || null,
    };
    if (v.passwort_entfernen) {
      payload.password = '';
    } else if (v.password) {
      payload.password = v.password;
    }
    this.credBusy.set(true);
    this.credFehler.set(null);
    this.svc.setCredentials(c.id, payload).subscribe({
      next: (s) => {
        this.credBusy.set(false);
        this.credStatus.set(s);
        this.credOffen.set(false);
      },
      error: (err: unknown) => {
        this.credBusy.set(false);
        this.credFehler.set(fehlerDetail(err) ?? 'Zugangsdaten konnten nicht gespeichert werden.');
      },
    });
  }

  statusUmschalten(c: SupplierConnection): void {
    if (this.dialogBusy() || !this.darfAendern()) return;
    this.listenFehler.set(null);
    const neuStatus = c.status === 'ACTIVE' ? 'INACTIVE' : 'ACTIVE';
    this.svc.update(c.id, { status: neuStatus }).subscribe({
      next: () => this.neuLaden(),
      error: (err: unknown) =>
        this.listenFehler.set(
          fehlerDetail(err) ?? 'Der Status konnte nicht geändert werden.',
        ),
    });
  }

  // ---- Darstellungshelfer ---------------------------------------------------
  systemLabel(s: string): string {
    return sourceSystemLabel(s);
  }
  kindLabel(k: string): string {
    return kindLabel(k);
  }
  statusLabel(s: string): string {
    return statusLabel(s);
  }
  importDatum(iso: string): string {
    const d = new Date(iso);
    return isNaN(d.getTime())
      ? iso
      : d.toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' });
  }
}
