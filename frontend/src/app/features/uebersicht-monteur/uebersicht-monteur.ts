import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { forkJoin, of } from 'rxjs';
import { catchError, map, switchMap } from 'rxjs/operators';

import { AufgabeService } from '../../core/aufgabe.service';
import { EinsatzService } from '../../core/einsatz.service';
import { PartyService } from '../../core/party.service';
import { PropertyService } from '../../core/property.service';
import { ZeiterfassungService } from '../../core/zeiterfassung.service';
import { AuthService } from '../../core/auth.service';
import { Task } from '../../core/aufgabe.model';
import {
  ServiceJob,
  ServiceJobStatus,
  serviceJobStatusClass,
  serviceJobStatusLabel,
} from '../../core/einsatz.model';
import { ContactPoint } from '../../core/party.model';
import { StempelZustand } from '../../core/zeiterfassung.model';
import { isoDatumDe } from '../../shared/datum';

/**
 * Ein Einsatz, angereichert um das, was der Monteur an der Tür braucht:
 * Anschrift des Objekts, Zutrittshinweis und der Vor-Ort-Kontakt mit Rufnummer.
 *
 * Der Server liefert das über drei bestehende Endpunkte (Einsatz-Detail,
 * Liegenschaft, Kontaktwege der Party) — hier wird nur zusammengeführt, nicht
 * nachgerechnet. Schlägt eine der Anreicherungen fehl (fehlendes Recht,
 * fehlende Daten), bleibt das Feld schlicht leer: der Einsatz selbst steht
 * trotzdem da. Ein fehlender Zusatz darf den Arbeitstag nicht verstecken.
 */
export interface EinsatzKarte {
  job: ServiceJob;
  adresse: string | null;
  zutritt: string | null;
  kontaktName: string | null;
  /** Rufnummern des Vor-Ort-Kontakts (PHONE/MOBILE), primäre zuerst. */
  rufnummern: { label: string; wert: string; tel: string }[];
}

type Zustand<T> = { kind: 'loading' } | { kind: 'ready'; daten: T } | { kind: 'error' };

/** Status, in denen ein Einsatz „läuft" — dort führt der Weg in den Bericht. */
const LAUFEND: ServiceJobStatus[] = ['UNTERWEGS', 'VOR_ORT', 'PAUSIERT'];

/** Lokales `YYYY-MM-DD` (nicht UTC — sonst kippt der Tag abends um). */
function isoTag(d: Date): string {
  return [
    d.getFullYear(),
    String(d.getMonth() + 1).padStart(2, '0'),
    String(d.getDate()).padStart(2, '0'),
  ].join('-');
}

/** Tagesgrenze als ISO-Zeitpunkt in LOKALER Zeit (der Server rechnet um). */
function tagesGrenze(d: Date, ende: boolean): string {
  const g = new Date(d);
  g.setHours(ende ? 23 : 0, ende ? 59 : 0, ende ? 59 : 0, 0);
  return g.toISOString();
}

/**
 * Die Startseite des Monteurs.
 *
 * Kein Geld, keine Projekte, kein Onboarding — das ist Büro-Kram und für ihn
 * ohnehin gesperrt. Hier steht, was er heute braucht: seine Einsätze mit
 * Adresse und Wähl-Link, seine nächsten Termine, seine Aufgaben, die Stempeluhr
 * und der Sprung in den Baustellenbericht des laufenden Einsatzes.
 *
 * Alle Abfragen laufen über Endpunkte, die den row_scope EIGENE auswerten
 * (`require_scoped`) — die Zeilenbegrenzung macht der Server, nicht dieses
 * Bauteil.
 */
@Component({
  selector: 'app-uebersicht-monteur',
  imports: [RouterLink],
  templateUrl: './uebersicht-monteur.html',
  styleUrl: './uebersicht-monteur.scss',
})
export class UebersichtMonteur {
  private readonly einsatzSvc = inject(EinsatzService);
  private readonly propertySvc = inject(PropertyService);
  private readonly partySvc = inject(PartyService);
  private readonly aufgabeSvc = inject(AufgabeService);
  private readonly zeitSvc = inject(ZeiterfassungService);
  private readonly auth = inject(AuthService);

  protected readonly heute = signal<Zustand<EinsatzKarte[]>>({ kind: 'loading' });
  protected readonly naechste = signal<Zustand<ServiceJob[]>>({ kind: 'loading' });
  protected readonly aufgaben = signal<Zustand<{ total: number; items: Task[] }>>({
    kind: 'loading',
  });
  protected readonly stempel = signal<Zustand<StempelZustand>>({ kind: 'loading' });

  protected readonly heuteText = new Intl.DateTimeFormat('de-DE', {
    weekday: 'long',
    day: '2-digit',
    month: 'long',
  }).format(new Date());

  protected readonly vorname = computed(() => {
    const name = this.auth.user()?.display_name ?? '';
    // „Timo Kalinski (Login)" → „Timo". Der Anzeigename ist nicht zerlegt.
    return name.split(/\s+/)[0] || 'Willkommen';
  });

  /** Der laufende Einsatz — dort führt der Weg direkt in den Bericht. */
  protected readonly laufend = computed(() => {
    const h = this.heute();
    if (h.kind !== 'ready') return null;
    return h.daten.find((k) => LAUFEND.includes(k.job.status)) ?? null;
  });

  constructor() {
    this.laden();
  }

  laden(): void {
    this.einsaetzeHeuteLaden();
    this.naechsteLaden();

    this.aufgaben.set({ kind: 'loading' });
    this.aufgabeSvc.list({ page: 1, page_size: 5, status: 'OFFEN' }).subscribe({
      next: (d) => this.aufgaben.set({ kind: 'ready', daten: { total: d.total, items: d.items } }),
      error: () => this.aufgaben.set({ kind: 'error' }),
    });

    this.stempel.set({ kind: 'loading' });
    this.zeitSvc.aktuell().subscribe({
      next: (s) => this.stempel.set({ kind: 'ready', daten: s }),
      error: () => this.stempel.set({ kind: 'error' }),
    });
  }

  /**
   * Die Einsätze von heute — und dann, je Einsatz, die Anreicherung.
   *
   * Bewusst nacheinander (Liste → Details): erst die Liste sagt, welche Objekte
   * und Kontakte überhaupt gebraucht werden. Jede Anreicherung fängt ihren
   * eigenen Fehler ab (`catchError` → null), damit ein fehlendes Detail nie den
   * ganzen Arbeitstag aus der Ansicht wirft.
   */
  private einsaetzeHeuteLaden(): void {
    const jetzt = new Date();
    this.heute.set({ kind: 'loading' });
    this.einsatzSvc
      .list({
        page: 1,
        page_size: 50,
        scheduled_from: tagesGrenze(jetzt, false),
        scheduled_to: tagesGrenze(jetzt, true),
      })
      .pipe(
        switchMap((seite) => {
          const jobs = [...seite.items].sort((a, b) =>
            (a.scheduled_start ?? '').localeCompare(b.scheduled_start ?? ''),
          );
          if (jobs.length === 0) return of([] as EinsatzKarte[]);
          return forkJoin(jobs.map((j) => this.karteBauen(j)));
        }),
      )
      .subscribe({
        next: (karten) => this.heute.set({ kind: 'ready', daten: karten }),
        error: () => this.heute.set({ kind: 'error' }),
      });
  }

  /** Ein Einsatz + Objektanschrift + Zutritt + Vor-Ort-Kontakt mit Rufnummer. */
  private karteBauen(job: ServiceJob) {
    const adresse$ = job.property
      ? this.propertySvc.get(job.property.id).pipe(
          map((p) => {
            const a = p.address;
            const zeile1 = [a.street, a.house_number].filter(Boolean).join(' ');
            const zeile2 = [a.postal_code, a.city].filter(Boolean).join(' ');
            return [zeile1, zeile2].filter(Boolean).join(', ') || null;
          }),
          catchError(() => of(null)),
        )
      : of(null);

    const detail$ = this.einsatzSvc.get(job.id).pipe(catchError(() => of(null)));

    return detail$.pipe(
      switchMap((detail) => {
        const partyId = detail?.on_site_contact_party_id ?? null;
        const wege$ = partyId
          ? this.partySvc.listContactPoints(partyId).pipe(catchError(() => of([] as ContactPoint[])))
          : of([] as ContactPoint[]);
        return forkJoin({ adresse: adresse$, wege: wege$ }).pipe(
          map(
            ({ adresse, wege }): EinsatzKarte => ({
              job,
              adresse,
              zutritt: detail?.access_instructions ?? null,
              kontaktName: detail?.on_site_contact ?? null,
              rufnummern: this.rufnummern(wege),
            }),
          ),
        );
      }),
    );
  }

  /** Telefonische Kontaktwege, primäre zuerst — als `tel:`-taugliche Ziele. */
  private rufnummern(wege: ContactPoint[]): EinsatzKarte['rufnummern'] {
    return wege
      .filter(
        (w) =>
          (w.contact_type === 'MOBILE' || w.contact_type === 'PHONE') && w.valid_until === null,
      )
      .sort((a, b) => Number(b.is_primary) - Number(a.is_primary))
      .map((w) => ({
        label: w.label ?? (w.contact_type === 'MOBILE' ? 'Mobil' : 'Telefon'),
        wert: w.value,
        // `tel:` verträgt keine Leerzeichen/Klammern; Ziffern und führendes + bleiben.
        tel: w.value.replace(/[^\d+]/g, ''),
      }));
  }

  private naechsteLaden(): void {
    const morgen = new Date();
    morgen.setDate(morgen.getDate() + 1);
    const bis = new Date();
    bis.setDate(bis.getDate() + 21);
    this.naechste.set({ kind: 'loading' });
    this.einsatzSvc
      .list({
        page: 1,
        page_size: 8,
        scheduled_from: tagesGrenze(morgen, false),
        scheduled_to: tagesGrenze(bis, true),
      })
      .subscribe({
        next: (seite) =>
          this.naechste.set({
            kind: 'ready',
            daten: [...seite.items].sort((a, b) =>
              (a.scheduled_start ?? '').localeCompare(b.scheduled_start ?? ''),
            ),
          }),
        error: () => this.naechste.set({ kind: 'error' }),
      });
  }

  // ---- Darstellung --------------------------------------------------------

  /** '2026-07-14T09:00:00Z' → '09:00'; ohne Zeit ein Gedankenstrich. */
  uhrzeit(iso: string | null): string {
    if (!iso) return '—';
    const d = new Date(iso);
    return Number.isNaN(d.getTime())
      ? '—'
      : new Intl.DateTimeFormat('de-DE', { hour: '2-digit', minute: '2-digit' }).format(d);
  }

  /** Zeitfenster eines Einsatzes, z. B. '09:00 – 12:00' oder 'ohne Uhrzeit'. */
  fenster(job: ServiceJob): string {
    if (!job.scheduled_start) return 'ohne Uhrzeit';
    const von = this.uhrzeit(job.scheduled_start);
    return job.scheduled_end ? `${von} – ${this.uhrzeit(job.scheduled_end)}` : `ab ${von}`;
  }

  /** Tag + Uhrzeit für die Vorschau der nächsten Termine. */
  tagUndZeit(iso: string | null): string {
    if (!iso) return 'ohne Termin';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return 'ohne Termin';
    const tag = new Intl.DateTimeFormat('de-DE', {
      weekday: 'short',
      day: '2-digit',
      month: '2-digit',
    }).format(d);
    return `${tag} · ${this.uhrzeit(iso)}`;
  }

  statusLabel = serviceJobStatusLabel;
  statusClass = serviceJobStatusClass;
  datumDe = isoDatumDe;

  /** Sekunden → '7:30 h'. Gleiche Lesart wie in „Meine Zeiten". */
  dauerText(sekunden: number): string {
    const m = Math.max(0, Math.round(sekunden / 60));
    return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')} h`;
  }

  /** Der Tag, auf den sich die Stempelsummen beziehen (kann der Vortag sein). */
  bezugstag(s: StempelZustand): string {
    if (!s.tag) return 'heute';
    return s.tag === isoTag(new Date()) ? 'heute' : s.tag.split('-').reverse().join('.');
  }
}
