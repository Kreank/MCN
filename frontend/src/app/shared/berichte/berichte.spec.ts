import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { AuthService } from '../../core/auth.service';
import { SiteReport, SiteReportStatus } from '../../core/site-report.model';
import { Berichte } from './berichte';

/**
 * Die Protokoll-Maske nach dem Umbau vom 2026-08-03.
 *
 * Sascha beim Testen (2026-08-02): *„Das Zusammenklicken geht mir tatsächlich
 * bisschen auf die Nerven. Als ich dann fertig war, hab ich den Entwurf gesehen.
 * Können wir das nicht so machen, dass wenn ich auf den Button Protokoll klicke,
 * genau dieses Entwurffenster auftaucht?"*
 *
 * Geprüft wird genau das — und die drei Fallen, die dabei entstehen:
 *
 * 1. Ein Klick **legt an** (kein vorgeschaltetes Formular mehr).
 * 2. Die laufende Eingabe überlebt ein Nachladen des Details. Ohne diesen
 *    Schutz verlöre man seinen Satz mitten im Wort, sobald der Server antwortet.
 * 3. Die Startwahl erscheint **nur**, wenn es ein Angebot zu übernehmen gibt —
 *    und nie am freien Termin (dort gibt es keinen Auftrag und damit keines).
 */
const bericht = (
  id: string,
  status: SiteReportStatus = 'ENTWURF',
  ueberschreiben: Partial<SiteReport> = {},
): SiteReport => ({
  id,
  kopf: null,
  work_order_id: 'wo-1',
  service_job_id: 'sj-1',
  report_date: '2026-08-03',
  author_id: null,
  author_name: 'Monteur',
  weather: null,
  activity_text: 'Protokoll vom 03.08.2026',
  hours_worked: null,
  materials_note: null,
  remarks: null,
  status,
  signed_by_name: null,
  signed_at: null,
  signature_file_id: null,
  version: 1,
  created_at: '2026-08-03T08:00:00Z',
  ...ueberschreiben,
});

describe('Berichte — ein Klick, und der Entwurf steht da', () => {
  let fixture: ComponentFixture<Berichte>;
  let http: HttpTestingController;

  const el = () => fixture.nativeElement as HTMLElement;
  const text = () => (el().textContent ?? '').replace(/\s+/g, ' ');
  const knopf = (label: string) =>
    Array.from(el().querySelectorAll('button')).find((b) =>
      (b.textContent ?? '').includes(label),
    ) as HTMLButtonElement | undefined;
  /**
   * Das Eingabefeld „Ausgeführte Arbeiten" — gezielt über die Hauptspalte des
   * Blattes gesucht, nicht als „erste textarea im DOM": Positions-Editor und
   * Dialoge bringen eigene mit, und die stecken alle gleichzeitig im Baum.
   */
  const arbeitenFeld = () =>
    el().querySelector('.protokoll-raster__haupt textarea') as HTMLTextAreaElement | null;

  /**
   * Steht der Dialog wirklich offen?
   *
   * `app-dialog` projiziert seinen Inhalt IMMER — geschlossen ist er nur ein
   * `<dialog>` ohne `open`. Eine Prüfung gegen den Gesamttext fände die
   * Dialogüberschrift deshalb auch dann, wenn niemand sie sieht.
   */
  const dialogOffen = (ueberschrift: string) =>
    Array.from(el().querySelectorAll('dialog')).some(
      (d) => d.hasAttribute('open') && (d.textContent ?? '').includes(ueberschrift),
    );

  /**
   * Beantwortet die Listenabfrage des gewählten Ankers.
   *
   * `match` statt `expectOne`: Im Blatt stecken eigenständige Kindkomponenten
   * (gebuchte Zeiten, Dateiablage, Positionen), die beim Rendern ihre eigenen
   * Abrufe starten. Ein strikter Erwartungswert würde an denen scheitern, ohne
   * dass er etwas über den geprüften Fluss aussagt.
   */
  const listeAntworten = (items: SiteReport[]) => {
    const treffer = http.match(
      (r) => r.method === 'GET' && /\/site_reports(\?|$)/.test(r.url),
    );
    treffer.forEach((req) => req.flush({ items, total: items.length }));
    fixture.detectChanges();
  };

  /** Beantwortet das Nachladen des Details (Briefkopf). */
  const detailAntworten = (r: SiteReport) => {
    const treffer = http.match(
      (q) => q.method === 'GET' && q.url.endsWith(`/site_reports/${r.id}`),
    );
    treffer.forEach((req) => req.flush(r));
    fixture.detectChanges();
  };

  /**
   * Alle noch offenen Abrufe der Kindkomponenten abräumen.
   *
   * Die Antwortform muss passen: Die Dateiablage liest ihre Kategorien als
   * blankes Array und stolperte über ein `{items}`-Objekt — ein Fehler im
   * Testgerüst, der wie ein Fehler im Produkt aussieht.
   */
  const restAbraeumen = () => {
    http.match(() => true).forEach((r) => {
      if (r.cancelled) return;
      r.flush(/file-categories|vorbelegen-angebote/.test(r.request.url) ? [] : { items: [], total: 0 });
    });
  };

  beforeEach(async () => {
    // Das Unterschrift-Pad liegt IMMER im DOM (der Dialog projiziert seinen
    // Inhalt auch geschlossen, Schließen ist nur `display: none`) und beobachtet
    // seine Canvas-Größe. Die Testumgebung bringt keinen ResizeObserver mit —
    // ohne diesen Stub scheitert jede Prüfung an einer Zeichenfläche, um die es
    // hier gar nicht geht.
    (globalThis as Record<string, unknown>)['ResizeObserver'] ??= class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    };

    await TestBed.configureTestingModule({
      imports: [Berichte],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
        { provide: AuthService, useValue: { darf: () => true, darfAlle: () => true } },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(Berichte);
    http = TestBed.inject(HttpTestingController);
    fixture.componentRef.setInput('serviceJobId', 'sj-1');
  });

  afterEach(() => {
    restAbraeumen();
  });

  it('legt beim Klick sofort an — ohne vorgeschalteten Formular-Dialog', () => {
    fixture.detectChanges();
    listeAntworten([]);

    knopf('Neues Protokoll')!.click();
    fixture.detectChanges();

    const angelegt = http.match(
      (r) => r.method === 'POST' && r.url.endsWith('/site_reports'),
    );
    expect(angelegt.length).toBe(1);
    // Der Termin steht fest — er wird mitgeschickt, nicht abgefragt.
    expect(angelegt[0].request.body.service_job_id).toBe('sj-1');
    // `activity_text` ist in der DB Pflicht (CHECK 0054): ohne Vorbelegung
    // ließe sich der Entwurf gar nicht anlegen.
    expect(angelegt[0].request.body.activity_text).toBeTruthy();
    angelegt[0].flush(bericht('r-1'));
  });

  it('zeigt den Entwurf danach als bearbeitbares Blatt', () => {
    fixture.detectChanges();
    listeAntworten([]);
    knopf('Neues Protokoll')!.click();
    fixture.detectChanges();

    http
      .match((r) => r.method === 'POST' && r.url.endsWith('/site_reports'))
      .forEach((r) => r.flush(bericht('r-1')));
    fixture.detectChanges();
    detailAntworten(bericht('r-1'));
    // Kein Auftrag-Angebot vorhanden → Startwahl bleibt aus.
    http
      .match((r) => r.url.endsWith('/vorbelegen-angebote'))
      .forEach((r) => r.flush([]));
    fixture.detectChanges();

    // Die ausgeführten Arbeiten sind ein EINGABEFELD, kein Fließtext.
    expect(arbeitenFeld()).not.toBeNull();
    expect(knopf('Bericht speichern')).toBeDefined();
    // Der Umweg über einen Bearbeiten-Dialog ist entfallen.
    expect(knopf('Bearbeiten')).toBeUndefined();
  });

  it('wirft die laufende Eingabe NICHT weg, wenn das Detail nachlädt', () => {
    fixture.detectChanges();
    listeAntworten([bericht('r-1')]);
    detailAntworten(bericht('r-1'));

    const feld = arbeitenFeld()!;
    feld.value = 'Steigstrang im 2. OG getauscht';
    feld.dispatchEvent(new Event('input'));
    fixture.detectChanges();

    // Ein zweites Detail-Nachladen (passiert nach jedem Speichern und beim
    // Zurückkehren in den Reiter) darf den Satz nicht überschreiben.
    fixture.componentInstance.neuLaden();
    fixture.detectChanges();
    listeAntworten([bericht('r-1')]);
    detailAntworten(bericht('r-1'));

    expect(arbeitenFeld()!.value).toBe('Steigstrang im 2. OG getauscht');
  });

  it('fragt nach dem Anlegen, ob aus einem Angebot übernommen wird', () => {
    fixture.detectChanges();
    listeAntworten([]);
    knopf('Neues Protokoll')!.click();
    fixture.detectChanges();
    http
      .match((r) => r.method === 'POST' && r.url.endsWith('/site_reports'))
      .forEach((r) => r.flush(bericht('r-1')));
    fixture.detectChanges();
    detailAntworten(bericht('r-1'));

    http
      .match((r) => r.url.endsWith('/vorbelegen-angebote'))
      .forEach((r) =>
        r.flush([
          { id: 'q-1', quote_number: 'AN-26-0001', title: 'Bad WE 12', status: 'VERSENDET' },
        ]),
      );
    fixture.detectChanges();

    expect(dialogOffen('Womit möchten Sie beginnen?')).toBe(true);
    expect(text()).toContain('Bad WE 12');
    expect(knopf('Leer beginnen')).toBeDefined();
  });

  it('fragt am freien Termin gar nicht erst — dort gibt es kein Angebot', () => {
    fixture.detectChanges();
    listeAntworten([]);
    knopf('Neues Protokoll')!.click();
    fixture.detectChanges();
    // Bericht OHNE Auftrag (Begehung).
    http
      .match((r) => r.method === 'POST' && r.url.endsWith('/site_reports'))
      .forEach((r) => r.flush(bericht('r-1', 'ENTWURF', { work_order_id: null })));
    fixture.detectChanges();
    detailAntworten(bericht('r-1', 'ENTWURF', { work_order_id: null }));

    // Der Abruf der vorbelegbaren Angebote findet gar nicht erst statt.
    expect(http.match((r) => r.url.endsWith('/vorbelegen-angebote')).length).toBe(0);
    expect(dialogOffen('Womit möchten Sie beginnen?')).toBe(false);
  });

  it('zeigt den unterzeichneten Bericht als Fließtext, nicht als Maske', () => {
    fixture.detectChanges();
    const fertig = bericht('r-1', 'UNTERZEICHNET', {
      activity_text: 'Alles erledigt',
      signed_by_name: 'K. Meier',
      signed_at: '2026-08-03T10:00:00Z',
    });
    listeAntworten([fertig]);
    detailAntworten(fertig);

    expect(arbeitenFeld()).toBeNull();
    expect(text()).toContain('Alles erledigt');
    expect(knopf('Bericht speichern')).toBeUndefined();
  });

  it('bietet kein Feld „Material (Notiz)" mehr an — Material gehört in die Positionen', () => {
    fixture.detectChanges();
    listeAntworten([bericht('r-1')]);
    detailAntworten(bericht('r-1'));

    const beschriftungen = Array.from(el().querySelectorAll('label')).map(
      (l) => l.textContent ?? '',
    );
    expect(beschriftungen.some((b) => b.includes('Material'))).toBe(false);
  });

  it('zeigt eine ALTE Materialnotiz weiterhin an — sie geht nicht verloren', () => {
    fixture.detectChanges();
    const mitNotiz = bericht('r-1', 'ENTWURF', { materials_note: '3 m Kupferrohr' });
    listeAntworten([mitNotiz]);
    detailAntworten(mitNotiz);

    expect(text()).toContain('3 m Kupferrohr');
  });
});
