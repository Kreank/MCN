import {
  Component,
  DestroyRef,
  ElementRef,
  computed,
  effect,
  inject,
  input,
  model,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { HttpEventType } from '@angular/common/http';
import { Subscription } from 'rxjs';
import { AnbindungService } from '../../../core/anbindung.service';
import {
  DatanormImportErgebnis,
  SupplierConnection,
} from '../../../core/anbindung.model';
import { Dialog } from '../../../shared/dialog/dialog';
import { fehlerDetail } from '../../../shared/http-fehler';

/**
 * DATANORM-Import-Dialog für eine Lieferanten-Anbindung. Der Anwender wählt die
 * Stammdatei (Pflicht) und optional die Preisdatei (jeweils ZIP oder rohe
 * DATANORM-Datei), lässt sich per **Vorschau** (dry-run, schreibt nichts) zeigen,
 * was passieren würde, und stößt dann den Import an. Angelegt/aktualisiert/
 * deaktiviert + Beispiele + Fehler kommen vom Server.
 *
 * Große Vollkataloge (mehrere GB) laufen bewusst über das CLI-Kommando — der
 * Upload ist auf ~80 MB begrenzt; der Hinweis steht im Dialog. Während des
 * Uploads wird ein Fortschritt angezeigt; der Vorgang lässt sich abbrechen (der
 * Dialog ist nie „eingesperrt").
 */
@Component({
  selector: 'app-datanorm-import',
  imports: [Dialog],
  templateUrl: './datanorm-import.html',
  styleUrl: './datanorm-import.scss',
})
export class DatanormImport {
  private readonly svc = inject(AnbindungService);
  private readonly destroyRef = inject(DestroyRef);

  readonly connection = input<SupplierConnection | null>(null);
  readonly offen = model(false);
  /** Nach einem echten (nicht dry-run) Import: der Aufrufer lädt die Liste neu. */
  readonly importiert = output<void>();

  private readonly ergebnisPanel =
    viewChild<ElementRef<HTMLElement>>('ergebnisPanel');

  protected readonly stamm = signal<File | null>(null);
  protected readonly preise = signal<File | null>(null);
  protected readonly busy = signal(false);
  /** Upload-Fortschritt 0–100, oder null (Server verarbeitet / unbestimmt). */
  protected readonly prozent = signal<number | null>(null);
  protected readonly fehler = signal<string | null>(null);
  protected readonly ergebnis = signal<DatanormImportErgebnis | null>(null);
  /** Nach einem echten Import ist der Vorgang abgeschlossen (kein erneuter Lauf). */
  protected readonly abgeschlossen = signal(false);

  private laufend?: Subscription;

  protected readonly kannStarten = computed(
    () => !!this.stamm() && !this.busy() && !this.abgeschlossen(),
  );

  constructor() {
    // Beim Öffnen zurücksetzen.
    effect(() => {
      if (this.offen()) this.zuruecksetzen();
      else this.abbrechen();
    });
    this.destroyRef.onDestroy(() => this.abbrechen());
  }

  private zuruecksetzen(): void {
    this.abbrechen();
    this.stamm.set(null);
    this.preise.set(null);
    this.fehler.set(null);
    this.ergebnis.set(null);
    this.abgeschlossen.set(false);
    this.prozent.set(null);
    this.busy.set(false);
  }

  private abbrechen(): void {
    this.laufend?.unsubscribe();
    this.laufend = undefined;
    this.busy.set(false);
    this.prozent.set(null);
  }

  stammGewaehlt(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    this.stamm.set(input.files?.[0] ?? null);
    this.ergebnis.set(null);
    this.abgeschlossen.set(false);
    // Zurücksetzen, damit dieselbe Datei erneut gewählt werden kann (change feuert
    // sonst nicht). Der Name wird über das Signal angezeigt, nicht über den Input.
    input.value = '';
  }

  preiseGewaehlt(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    this.preise.set(input.files?.[0] ?? null);
    this.ergebnis.set(null);
    this.abgeschlossen.set(false);
    input.value = '';
  }

  schliessen(): void {
    // Bewusst NICHT durch `busy` blockiert: ein laufender Upload wird abgebrochen,
    // damit der Dialog nie „eingesperrt" ist (Netzwerk-Hänger, langer Import).
    this.abbrechen();
    this.offen.set(false);
  }

  vorschau(): void {
    this.starten(true);
  }

  importieren(): void {
    this.starten(false);
  }

  private starten(dryRun: boolean): void {
    const conn = this.connection();
    const stamm = this.stamm();
    if (!conn || !stamm || this.busy()) return;
    this.busy.set(true);
    this.fehler.set(null);
    this.ergebnis.set(null);
    this.prozent.set(0);
    this.laufend = this.svc
      .datanormImport(conn.id, stamm, this.preise(), dryRun)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (ev) => {
          if (ev.type === HttpEventType.UploadProgress) {
            // Nach dem Upload verarbeitet der Server (ohne Fortschritt) → null.
            this.prozent.set(
              ev.total ? Math.round((ev.loaded / ev.total) * 100) : null,
            );
            if (ev.total && ev.loaded >= ev.total) this.prozent.set(null);
          } else if (ev.type === HttpEventType.Response && ev.body) {
            this.busy.set(false);
            this.prozent.set(null);
            this.laufend = undefined;
            this.ergebnis.set(ev.body);
            if (!dryRun) {
              this.abgeschlossen.set(true);
              this.importiert.emit();
            }
            // Ergebnis für Screenreader/Tastatur fokussieren (frisch eingefügt).
            setTimeout(() => this.ergebnisPanel()?.nativeElement.focus(), 0);
          }
        },
        error: (err: unknown) => {
          this.busy.set(false);
          this.prozent.set(null);
          this.laufend = undefined;
          this.fehler.set(fehlerDetail(err) ?? 'Der Import ist fehlgeschlagen.');
        },
      });
  }

  // Anzeige
  aktionLabel(a: string): string {
    const map: Record<string, string> = {
      angelegt: 'Neu',
      aktualisiert: 'Aktualisiert',
      deaktiviert: 'Deaktiviert',
    };
    return map[a] ?? a;
  }

  private readonly euroFmt = new Intl.NumberFormat('de-DE', {
    style: 'currency',
    currency: 'EUR',
    maximumFractionDigits: 4,
  });
  preis(wert: string | null): string {
    if (wert === null) return '—';
    const n = Number(wert);
    return isNaN(n) ? `${wert} €` : this.euroFmt.format(n);
  }
}
