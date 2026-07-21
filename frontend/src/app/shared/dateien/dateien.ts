import { HttpErrorResponse, HttpEventType } from '@angular/common/http';
import {
  Component,
  DestroyRef,
  ElementRef,
  computed,
  effect,
  inject,
  input,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { map } from 'rxjs';
import { AuthService } from '../../core/auth.service';
import { Datei, LinkKategorie, ZielFilter } from '../../core/datei.model';
import { DateiService } from '../../core/datei.service';
import { VerbotenState, fehlerDetail, fehlerState } from '../http-fehler';
import { Bestaetigung } from '../bestaetigung/bestaetigung';

type Zustand =
  | { kind: 'loading' }
  | { kind: 'ready'; items: Datei[] }
  | VerbotenState
  | { kind: 'error' };

/** Ein fehlgeschlagener Upload — Dateiname + Servermeldung (i. d. R. 422). */
interface UploadFehler {
  name: string;
  text: string;
}

/** Auswaehlbare fachliche Kategorie (BELEG_PDF ist reserviert, nur beleg_pdf.py). */
interface KategorieOption {
  wert: LinkKategorie;
  label: string;
}

/**
 * Wiederverwendbarer „Dateien"-Bereich fuer eine Detail-Mappe. Bindet die vier
 * Endpunkte von `/api/content` an ein Zielobjekt (`ziel`) an: Liste, Upload
 * (Auswahl + Drag&Drop, mit Fortschritt), Download (durch die Anwendung) und
 * Verknuepfung loesen (die Datei selbst bleibt).
 *
 * ```html
 * <app-dateien [ziel]="dateienZiel()" titel="Projektdateien" />
 * ```
 *
 * `ziel` sollte als STABILE Referenz uebergeben werden (z. B. ein `computed`),
 * damit der Lade-Effekt nicht bei jeder Change-Detection erneut feuert.
 */
@Component({
  selector: 'app-dateien',
  imports: [Bestaetigung],
  templateUrl: './dateien.html',
  styleUrl: './dateien.scss',
})
export class Dateien {
  private readonly svc = inject(DateiService);
  private readonly auth = inject(AuthService);
  private readonly destroyRef = inject(DestroyRef);

  /** Verwirft veraltete Liste-Antworten (Race beim schnellen Zielwechsel). */
  private ladeReqId = 0;

  /** Genau ein Zielfeld gesetzt (project_id, party_id, quote_id …). */
  readonly ziel = input.required<ZielFilter>();
  /** Optionale Ueberschrift ueber dem Bereich. */
  readonly titel = input<string>('');

  protected readonly zustand = signal<Zustand>({ kind: 'loading' });

  // --- Rechte (nur UI-Sichtbarkeit; der Server setzt sie ohnehin durch) -----
  protected readonly darfAnlegen = computed(() => this.auth.darf('content', 'ANLEGEN'));
  protected readonly darfLoesen = computed(() => this.auth.darf('content', 'AENDERN'));

  // --- Upload --------------------------------------------------------------
  protected readonly kategorie = signal<LinkKategorie>('DOKUMENT');
  protected readonly kategorien: KategorieOption[] = [
    { wert: 'DOKUMENT', label: 'Dokument' },
    { wert: 'FOTO_VORHER', label: 'Foto (vorher)' },
    { wert: 'FOTO_NACHHER', label: 'Foto (nachher)' },
    { wert: 'VIDEO_BEGEHUNG', label: 'Video (Begehung)' },
    { wert: 'SCAN', label: 'Scan' },
    { wert: 'PLAN', label: 'Plan' },
    { wert: 'VERTRAG', label: 'Vertrag' },
    { wert: 'SONSTIGES', label: 'Sonstiges' },
  ];

  protected readonly uploadLaeuft = signal(false);
  /** Fortschritt 0–100, oder null (unbestimmt/kein total gemeldet). */
  protected readonly uploadProzent = signal<number | null>(null);
  protected readonly uploadAktuell = signal<string | null>(null);
  protected readonly uploadIndex = signal(0);
  protected readonly uploadGesamt = signal(0);
  protected readonly uploadFehler = signal<UploadFehler[]>([]);
  protected readonly dragAktiv = signal(false);

  // --- Download-Fehler (aria-live) -----------------------------------------
  protected readonly downloadFehler = signal<string | null>(null);

  // --- Verknuepfung loesen (hinter Bestaetigung) ---------------------------
  protected readonly loesenZiel = signal<Datei | null>(null);
  protected readonly loesenLaeuft = signal(false);

  /** Ehrlicher Konsequenz-Text: nur die Verknuepfung geht, die Datei bleibt. */
  protected readonly loesenText = computed(() => {
    const name = this.loesenZiel()?.original_filename ?? '';
    return (
      `Die Verknüpfung von „${name}“ zu diesem Objekt wird entfernt. ` +
      'Die Datei selbst bleibt erhalten und kann an anderen Objekten weiter ' +
      'verknüpft sein.'
    );
  });

  constructor() {
    // Laedt (neu), sobald sich die Zielreferenz aendert. `ziel` muss dafuer
    // stabil uebergeben werden (computed), sonst laeuft der Effekt in einer
    // Schleife.
    effect(() => {
      const z = this.ziel();
      this.laden(z);
    });

    // Object-URLs der Bildvorschauen freigeben — sonst haelt der Browser jedes
    // je angesehene Bild im Speicher, solange die Seite lebt.
    this.destroyRef.onDestroy(() => this.urlsFreigeben());
  }

  protected readonly dateien = computed(() => {
    const z = this.zustand();
    return z.kind === 'ready' ? z.items : [];
  });

  neuLaden(): void {
    this.laden(this.ziel());
  }

  private laden(ziel: ZielFilter): void {
    const rid = ++this.ladeReqId;
    this.zustand.set({ kind: 'loading' });
    this.svc
      .liste(ziel)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (d) => {
          if (rid !== this.ladeReqId) return;
          this.zustand.set({ kind: 'ready', items: d.items });
          // Bildvorschauen nachziehen (Befund A1) — nur fuer Bilder und nur
          // unterhalb der Groessengrenze, siehe `vorschauenLaden`.
          this.vorschauenLaden(d.items);
        },
        error: (err) => {
          if (rid === this.ladeReqId) this.zustand.set(fehlerState(err));
        },
      });
  }

  // --- Datei-Auswahl / Drag&Drop -------------------------------------------
  dateiFeldGeaendert(event: Event): void {
    const input = event.target as HTMLInputElement;
    if (input.files && input.files.length > 0) {
      this.hochladen(Array.from(input.files));
    }
    // Zuruecksetzen, damit dieselbe Datei erneut gewaehlt werden kann.
    input.value = '';
  }

  onDragOver(event: DragEvent): void {
    if (!this.darfAnlegen() || this.uploadLaeuft()) return;
    event.preventDefault();
    this.dragAktiv.set(true);
  }

  onDragLeave(event: DragEvent): void {
    event.preventDefault();
    this.dragAktiv.set(false);
  }

  onDrop(event: DragEvent): void {
    event.preventDefault();
    this.dragAktiv.set(false);
    if (!this.darfAnlegen() || this.uploadLaeuft()) return;
    const dateien = event.dataTransfer?.files;
    if (dateien && dateien.length > 0) {
      this.hochladen(Array.from(dateien));
    }
  }

  // --- Upload (sequentiell, mit Fortschritt) -------------------------------
  private hochladen(dateien: File[]): void {
    if (this.uploadLaeuft() || dateien.length === 0) return;
    this.uploadFehler.set([]);
    this.downloadFehler.set(null);
    this.uploadLaeuft.set(true);
    this.uploadGesamt.set(dateien.length);
    this.naechste(dateien, 0);
  }

  private naechste(dateien: File[], index: number): void {
    if (index >= dateien.length) {
      this.uploadLaeuft.set(false);
      this.uploadProzent.set(null);
      this.uploadAktuell.set(null);
      this.uploadIndex.set(0);
      this.uploadGesamt.set(0);
      this.neuLaden();
      return;
    }
    const datei = dateien[index];
    this.uploadAktuell.set(datei.name);
    this.uploadIndex.set(index + 1);
    this.uploadProzent.set(0);
    this.svc
      .hochladen(this.ziel(), datei, this.kategorie())
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
      next: (ev) => {
        if (ev.type === HttpEventType.UploadProgress) {
          this.uploadProzent.set(
            ev.total ? Math.round((ev.loaded / ev.total) * 100) : null,
          );
        }
      },
      error: (err) => {
        this.uploadFehler.update((f) => [
          ...f,
          { name: datei.name, text: this.fehlerText(err) },
        ]);
        this.naechste(dateien, index + 1);
      },
      complete: () => this.naechste(dateien, index + 1),
    });
  }

  // --- Download ------------------------------------------------------------
  herunterladen(row: Datei): void {
    this.downloadFehler.set(null);
    this.svc
      .herunterladen(row.file_id, row.original_filename)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
      next: ({ blob, filename }) => this.blobAusloesen(blob, filename),
      error: (err) =>
        this.downloadFehler.set(
          fehlerDetail(err) ?? `„${row.original_filename}" konnte nicht geladen werden.`,
        ),
    });
  }

  /**
   * Object-URL + unsichtbarer `<a download>`. Die URL wird erst NACH dem
   * aktuellen Task freigegeben: ein synchrones `revokeObjectURL` direkt nach
   * `click()` bricht den Download in manchen Browsern ab, bevor er den Blob
   * gelesen hat. `try/finally` stellt sicher, dass die URL in jedem Pfad
   * (auch bei einem Fehler in `click()`) wieder freigegeben wird.
   */
  private blobAusloesen(blob: Blob, filename: string): void {
    const url = URL.createObjectURL(blob);
    try {
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.rel = 'noopener';
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (e) {
      URL.revokeObjectURL(url);
      throw e;
    }
  }

  // --- Verknuepfung loesen -------------------------------------------------
  loesenFragen(row: Datei): void {
    this.downloadFehler.set(null);
    this.loesenZiel.set(row);
  }

  loesenAbbrechen(): void {
    if (!this.loesenLaeuft()) this.loesenZiel.set(null);
  }

  loesenBestaetigen(): void {
    const row = this.loesenZiel();
    if (!row || this.loesenLaeuft()) return;
    this.loesenLaeuft.set(true);
    this.svc
      .verknuepfungLoesen(row.link_id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
      next: () => {
        this.loesenLaeuft.set(false);
        this.loesenZiel.set(null);
        // Aus der Liste entfernen, ohne kompletten Neuabruf.
        this.zustand.update((z) =>
          z.kind === 'ready'
            ? { kind: 'ready', items: z.items.filter((d) => d.link_id !== row.link_id) }
            : z,
        );
      },
      error: (err) => {
        this.loesenLaeuft.set(false);
        this.loesenZiel.set(null);
        this.downloadFehler.set(
          fehlerDetail(err) ?? 'Die Verknüpfung konnte nicht gelöst werden.',
        );
      },
    });
  }

  // --- Darstellungshelfer --------------------------------------------------
  private fehlerText(err: unknown): string {
    if (err instanceof HttpErrorResponse && err.status === 413) {
      return 'Die Datei ist zu groß.';
    }
    return fehlerDetail(err) ?? 'Der Upload ist fehlgeschlagen.';
  }

  groesse(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    const kb = bytes / 1024;
    if (kb < 1024) {
      return `${new Intl.NumberFormat('de-DE', { maximumFractionDigits: kb < 10 ? 1 : 0 }).format(kb)} KB`;
    }
    const mb = kb / 1024;
    return `${new Intl.NumberFormat('de-DE', { maximumFractionDigits: mb < 10 ? 1 : 0 }).format(mb)} MB`;
  }

  datum(iso: string): string {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' });
  }

  kategorieLabel(code: string | null): string {
    if (!code) return '';
    const map: Record<string, string> = {
      DOKUMENT: 'Dokument',
      FOTO_VORHER: 'Foto (vorher)',
      FOTO_NACHHER: 'Foto (nachher)',
      VIDEO_BEGEHUNG: 'Video',
      SCAN: 'Scan',
      PLAN: 'Plan',
      VERTRAG: 'Vertrag',
      SONSTIGES: 'Sonstiges',
      BELEG_PDF: 'Beleg-PDF',
    };
    return map[code] ?? code;
  }

  // --- Bildvorschau und Grossansicht (Befunde A1/A2/A3) --------------------
  //
  // Sascha: „Bilder sollten gleich sichtbar sein im Frontend. Ausserdem
  // klickbar! Wenn man drauf klickt, oeffnet sich das Bild in gross."
  // Bisher stand da nur das Kuerzel „IMG".
  //
  // Der Weg ist vorgezeichnet: Der Download-Endpunkt setzt bewusst
  // `Content-Disposition: attachment` (damit hochgeladener Inhalt nie im
  // Origin der Anwendung gerendert wird), also geht `<img src="/api/...">`
  // nicht. Stattdessen Blob durch die Anwendung holen und `createObjectURL` —
  // genau das Muster, das `artikel-detail` fuer das Artikelbild schon nutzt.
  //
  // **Die Kosten sind ehrlich zu nennen:** Es gibt keine Thumbnails
  // (`content.file.media_metadata` ist dafuer vorgesehen, wird aber leer
  // geschrieben). Jede Vorschau laedt die VOLLE Datei. Deshalb laden wir
  // automatisch nur unterhalb einer Grenze; groessere Bilder bekommen ihre
  // Vorschau erst beim Oeffnen der Grossansicht.
  private static readonly VORSCHAU_MAX_BYTES = 3_000_000;

  /** file_id → Object-URL. Wird beim Zerstoeren freigegeben. */
  protected readonly vorschauen = signal<Record<string, string>>({});
  private objectUrls: string[] = [];
  /** Der Schliessen-Knopf der Grossansicht (Fokusziel beim Oeffnen). */
  private readonly lightboxZu = viewChild<ElementRef<HTMLButtonElement>>('lightboxZu');
  /** Die Datei in der Grossansicht (null = zu). */
  protected readonly grossansicht = signal<Datei | null>(null);
  protected readonly grossansichtUrl = signal<string | null>(null);
  protected readonly grossansichtLaedt = signal(false);

  istBild(d: Datei): boolean {
    return d.mime_type.startsWith('image/');
  }

  /** Vorschauen der Bilder einer frisch geladenen Liste holen. */
  private vorschauenLaden(dateien: readonly Datei[]): void {
    for (const d of dateien) {
      if (!this.istBild(d)) continue;
      if (d.size_bytes > Dateien.VORSCHAU_MAX_BYTES) continue;
      if (this.vorschauen()[d.file_id]) continue;
      this.blobUrl(d).subscribe({
        next: (url) => {
          this.vorschauen.update((v) => ({ ...v, [d.file_id]: url }));
        },
        // Eine fehlgeschlagene Vorschau ist kein Fehlerzustand — die Zeile
        // faellt dann auf das Typkuerzel zurueck.
        error: () => {},
      });
    }
  }

  /** Laedt den Inhalt und gibt eine Object-URL zurueck (wird mitverwaltet). */
  private blobUrl(d: Datei) {
    return this.svc.herunterladen(d.file_id, d.original_filename).pipe(
      map(({ blob }: { blob: Blob }) => {
        const url = URL.createObjectURL(blob);
        this.objectUrls.push(url);
        return url;
      }),
    );
  }

  grossOeffnen(d: Datei): void {
    if (!this.istBild(d)) return;
    this.grossansicht.set(d);
    // Fokus ins Overlay holen — sonst stünde er hinter dem Bild in der Liste,
    // und Tastaturbedienung wie Screenreader landeten im Nichts.
    queueMicrotask(() => this.lightboxZu()?.nativeElement.focus());
    const vorhanden = this.vorschauen()[d.file_id];
    if (vorhanden) {
      this.grossansichtUrl.set(vorhanden);
      return;
    }
    // Grosses Bild: erst jetzt laden (siehe VORSCHAU_MAX_BYTES).
    this.grossansichtUrl.set(null);
    this.grossansichtLaedt.set(true);
    this.blobUrl(d).subscribe({
      next: (url) => {
        this.grossansichtLaedt.set(false);
        if (this.grossansicht()?.file_id === d.file_id) this.grossansichtUrl.set(url);
      },
      error: () => this.grossansichtLaedt.set(false),
    });
  }

  grossSchliessen(): void {
    this.grossansicht.set(null);
    this.grossansichtUrl.set(null);
  }

  private urlsFreigeben(): void {
    for (const url of this.objectUrls) URL.revokeObjectURL(url);
    this.objectUrls = [];
  }

  /** Grobes Icon-Kuerzel je MIME-Gruppe (nur dekorativ, aria-hidden). */
  typKuerzel(mime: string): string {
    if (mime === 'application/pdf') return 'PDF';
    if (mime.startsWith('image/')) return 'IMG';
    if (mime.startsWith('video/')) return 'VID';
    if (mime.includes('word') || mime.includes('opendocument.text')) return 'DOC';
    if (mime.includes('sheet') || mime.includes('excel')) return 'XLS';
    if (mime.includes('presentation') || mime.includes('powerpoint')) return 'PPT';
    if (mime === 'text/csv') return 'CSV';
    if (mime === 'text/plain') return 'TXT';
    if (mime === 'application/zip') return 'ZIP';
    return 'DAT';
  }
}
