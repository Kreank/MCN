import {
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  input,
  signal,
} from '@angular/core';
import { HttpErrorResponse, HttpEventType } from '@angular/common/http';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Datei } from '../../core/datei.model';
import { DateiService } from '../../core/datei.service';
import { fehlerDetail } from '../http-fehler';

/**
 * Arbeitsunfähigkeitsbescheinigung an einer Abwesenheit — **DSGVO Art. 9**.
 *
 * Bewusst NICHT `app-dateien` wiederverwendet, obwohl es dieselben vier
 * Endpunkte sind. Der allgemeine Dateibereich bietet eine Kategorie-Auswahl
 * („Foto vorher", „Plan", …) und einen „Verknüpfung lösen"-Knopf. Beides ist an
 * einem Gesundheitsdatum falsch: Die Kategorie setzt der **Server** fest
 * (`ATTEST`), und das Lösen ist Sache der Personalverwaltung
 * (Aufbewahrungsfrist), nicht des Beschäftigten. Ein Baustein, der genau eine
 * Sache tut, ist hier mehr wert als eine geteilte Komponente mit
 * Sonderfall-Flags.
 *
 * Was dieser Baustein NICHT tut und nie tun darf:
 * * **keine Diagnose erfassen** — es gibt kein Textfeld dafür, und es wird
 *   keines geben. Gespeichert wird „arbeitsunfähig von–bis", nicht *warum*.
 * * **keinen Dateinamen anzeigen, den der Nutzer gewählt hat** — der Server
 *   ersetzt ihn durch einen neutralen (`grippaler_infekt.pdf` verriete in jeder
 *   Liste die Diagnose). Was hier steht, kommt vom Server zurück.
 *
 * Die Rechteprüfung liegt vollständig im Server (`api/dateien.py`): Nur der
 * Betroffene selbst und die Personalverwaltung kommen an ein Attest; alles
 * andere ist 404. Dieses Fragment blendet nichts aus, was der Server preisgäbe —
 * es zeigt schlicht, was er liefert.
 */
@Component({
  selector: 'app-attest',
  imports: [],
  templateUrl: './attest.html',
  styleUrl: './attest.scss',
})
export class Attest {
  private readonly svc = inject(DateiService);
  private readonly destroyRef = inject(DestroyRef);

  /** Die Abwesenheit, an der das Attest hängt. */
  readonly absenceId = input.required<string>();
  /**
   * Darf hier hochgeladen werden? Der Server entscheidet ohnehin; das ist reine
   * Sichtbarkeit. Bei einer verworfenen Abwesenheit (abgelehnt/zurückgezogen)
   * lehnt die DB den Anhang ab — dann gar nicht erst anbieten.
   */
  readonly darfHochladen = input<boolean>(true);

  private ladeReqId = 0;

  protected readonly dateien = signal<Datei[]>([]);
  protected readonly laedt = signal(true);
  protected readonly fehler = signal<string | null>(null);
  protected readonly uploadLaeuft = signal(false);
  protected readonly uploadProzent = signal<number | null>(null);
  protected readonly meldung = signal('');
  protected readonly kannNicht = signal(false);

  protected readonly anzahl = computed(() => this.dateien().length);

  constructor() {
    effect(() => {
      const id = this.absenceId();
      if (id) this.laden(id);
    });
  }

  private laden(absenceId: string): void {
    const rid = ++this.ladeReqId;
    this.laedt.set(true);
    this.fehler.set(null);
    this.svc
      .liste({ absence_id: absenceId })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (d) => {
          if (rid !== this.ladeReqId) return;
          this.dateien.set(d.items);
          this.laedt.set(false);
          this.kannNicht.set(false);
        },
        error: (err: unknown) => {
          if (rid !== this.ladeReqId) return;
          this.laedt.set(false);
          // 404 heißt hier: nicht befugt (die Existenz wird nicht verraten) —
          // oder es gibt die Abwesenheit nicht. Beides ist dasselbe Ergebnis.
          this.kannNicht.set(
            err instanceof HttpErrorResponse && (err.status === 404 || err.status === 403),
          );
          this.dateien.set([]);
          if (!this.kannNicht()) {
            this.fehler.set(fehlerDetail(err) ?? 'Die Bescheinigungen konnten nicht geladen werden.');
          }
        },
      });
  }

  protected dateiGewaehlt(event: Event): void {
    const feld = event.target as HTMLInputElement;
    const datei = feld.files?.[0];
    feld.value = '';
    if (!datei) return;
    this.hochladen(datei);
  }

  private hochladen(datei: File): void {
    this.uploadLaeuft.set(true);
    this.uploadProzent.set(0);
    this.fehler.set(null);
    this.meldung.set('');
    // Die Kategorie setzt der Server auf ATTEST — was hier steht, ist egal und
    // wird verworfen. Wir schicken bewusst nichts Sprechendes mit.
    this.svc
      .hochladen({ absence_id: this.absenceId() }, datei, 'DOKUMENT')
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (ev) => {
          if (ev.type === HttpEventType.UploadProgress) {
            this.uploadProzent.set(ev.total ? Math.round((ev.loaded / ev.total) * 100) : null);
          }
          if (ev.type === HttpEventType.Response) {
            this.uploadLaeuft.set(false);
            this.uploadProzent.set(null);
            this.meldung.set('Bescheinigung hinterlegt.');
            this.laden(this.absenceId());
          }
        },
        error: (err: unknown) => {
          this.uploadLaeuft.set(false);
          this.uploadProzent.set(null);
          this.fehler.set(
            fehlerDetail(err) ?? 'Die Bescheinigung konnte nicht hinterlegt werden.',
          );
        },
      });
  }

  protected herunterladen(d: Datei): void {
    this.fehler.set(null);
    this.svc
      .herunterladen(d.file_id, d.original_filename)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: ({ blob, filename }) => {
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = filename;
          a.click();
          URL.revokeObjectURL(url);
        },
        error: (err: unknown) =>
          this.fehler.set(fehlerDetail(err) ?? 'Die Datei ist nicht abrufbar.'),
      });
  }

  protected groesse(bytes: number): string {
    const kb = bytes / 1024;
    return kb < 1024
      ? `${new Intl.NumberFormat('de-DE', { maximumFractionDigits: 0 }).format(kb)} kB`
      : `${new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 }).format(kb / 1024)} MB`;
  }

  protected datum(iso: string): string {
    return new Intl.DateTimeFormat('de-DE', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
    }).format(new Date(iso));
  }
}
