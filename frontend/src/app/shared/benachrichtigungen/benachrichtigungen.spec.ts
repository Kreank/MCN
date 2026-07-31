import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { signal } from '@angular/core';
import { AuthService } from '../../core/auth.service';
import { Benachrichtigungen } from './benachrichtigungen';
import { BenachrichtigungSeite } from '../../core/benachrichtigung.model';

/**
 * Die Glocke. Geprüft werden die Zusagen, an denen ein Postfach steht oder fällt:
 *
 * * Der Zähler nennt seine Zahl im **zugänglichen Namen**, nicht nur als Punkt —
 *   sonst existiert die Meldung für Screenreader nicht (WCAG 2.2 AA).
 * * Ein Netzfehler im Hintergrundtakt bleibt still: Eine Kopfzeile, die bei
 *   jedem Wackler Alarm schlägt, ist schlimmer als eine kurz veraltete Zahl.
 * * Beim Abmelden verschwindet der Zähler — sonst stünde auf der Anmeldeseite
 *   noch die Zahl des vorigen Kontos.
 */
const SEITE: BenachrichtigungSeite = {
  items: [
    {
      id: 'n-1',
      kind: 'AUFGABE_ERLEDIGT',
      title: 'Therme prüfen',
      body: 'Marius hat die Aufgabe erledigt.',
      target_type: 'workflow.task',
      target_id: 't-1',
      triggered_by: { id: 'u-2', display_name: 'Marius' },
      read_at: null,
      created_at: '2026-07-31T09:15:00+02:00',
    },
  ],
  total: 1,
  ungelesen: 1,
  page: 1,
  page_size: 20,
};

describe('Benachrichtigungen (Glocke)', () => {
  let fixture: ComponentFixture<Benachrichtigungen>;
  let http: HttpTestingController;
  const angemeldet = signal(true);

  const el = () => fixture.nativeElement as HTMLElement;

  beforeEach(async () => {
    angemeldet.set(true);
    await TestBed.configureTestingModule({
      imports: [Benachrichtigungen],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: AuthService, useValue: { istAngemeldet: angemeldet } },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(Benachrichtigungen);
    http = TestBed.inject(HttpTestingController);
    fixture.detectChanges();
  });

  afterEach(() => {
    fixture.destroy();
  });

  it('nennt die Zahl der ungelesenen Meldungen im zugänglichen Namen', () => {
    http.expectOne('/api/benachrichtigungen/zaehler').flush({ ungelesen: 3 });
    fixture.detectChanges();

    const knopf = el().querySelector('.glocke')!;
    expect(knopf.getAttribute('aria-label')).toBe('Benachrichtigungen, 3 ungelesen');
    expect(el().querySelector('.glocke__zahl')?.textContent?.trim()).toBe('3');
  });

  it('sagt auch das Nichts an', () => {
    http.expectOne('/api/benachrichtigungen/zaehler').flush({ ungelesen: 0 });
    fixture.detectChanges();

    expect(el().querySelector('.glocke')!.getAttribute('aria-label')).toBe(
      'Benachrichtigungen, keine ungelesenen',
    );
    expect(el().querySelector('.glocke__zahl')).toBeNull();
  });

  it('schluckt einen Fehler der Hintergrundabfrage', () => {
    http
      .expectOne('/api/benachrichtigungen/zaehler')
      .flush('kaputt', { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();

    expect(el().querySelector('[role="alert"]')).toBeNull();
    expect(el().querySelector('.glocke__zahl')).toBeNull();
  });

  it('lädt die Liste erst beim Öffnen und zeigt Art und Sache', () => {
    http.expectOne('/api/benachrichtigungen/zaehler').flush({ ungelesen: 1 });
    fixture.detectChanges();
    // Vor dem Öffnen wird die Liste NICHT geholt.
    http.expectNone((r) => r.url === '/api/benachrichtigungen');

    fixture.componentInstance.oeffnen();
    fixture.detectChanges();

    http.expectOne((r) => r.url === '/api/benachrichtigungen').flush(SEITE);
    fixture.detectChanges();

    // Die Art steht als Wort da, nicht nur als Farbe.
    expect(el().querySelector('.stamp')?.textContent?.trim()).toBe('Erledigt');
    expect(el().querySelector('.bn__sache')?.textContent?.trim()).toBe('Therme prüfen');
    expect(el().querySelector('.bn__neu')?.textContent?.trim()).toBe('neu');
  });

  it('meldet das Lesen und nimmt die Hervorhebung sofort weg', () => {
    http.expectOne('/api/benachrichtigungen/zaehler').flush({ ungelesen: 1 });
    fixture.componentInstance.oeffnen();
    fixture.detectChanges();
    http.expectOne((r) => r.url === '/api/benachrichtigungen').flush(SEITE);
    fixture.detectChanges();

    fixture.componentInstance.markieren(SEITE.items[0]);
    fixture.detectChanges();

    // Optimistisch: die Zeile ist sofort gelesen, ohne auf den Server zu warten.
    expect(el().querySelector('.bn__zeile--ungelesen')).toBeNull();
    http.expectOne('/api/benachrichtigungen/n-1/gelesen').flush({ ungelesen: 0 });
  });

  it('räumt den Zähler beim Abmelden ab', () => {
    http.expectOne('/api/benachrichtigungen/zaehler').flush({ ungelesen: 5 });
    fixture.detectChanges();

    angemeldet.set(false);
    fixture.detectChanges();

    expect(el().querySelector('.glocke__zahl')).toBeNull();
    // Nach dem Abmelden läuft kein Takt mehr.
    http.verify();
  });
});
