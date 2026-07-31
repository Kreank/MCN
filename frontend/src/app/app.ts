import {
  afterNextRender,
  Component,
  computed,
  effect,
  ElementRef,
  inject,
  Injector,
  signal,
} from '@angular/core';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs';
import { ThemeService } from './core/theme';
import { AuthService } from './core/auth.service';
import { Kommandopalette } from './shared/kommandopalette/kommandopalette';
import { Benachrichtigungen } from './shared/benachrichtigungen/benachrichtigungen';

const NAV_SPEICHER = 'mcn.nav.schmal';
/** Welche Navigationsgruppen der Nutzer zugeklappt hat (Liste von Gruppen-IDs). */
const NAV_ZU_SPEICHER = 'mcn.nav.zu';

interface NavItem {
  path: string;
  label: string;
  /** Kurzkennung fuer die Messkante-Bemaszung. */
  mark: string;
  /** Benötigtes Recht [Modul, Aktion]; ohne Angabe immer sichtbar. */
  recht?: readonly [string, string];
  /** Alternativ: sichtbar, sobald EINES dieser Rechte vorliegt (ODER-Logik). */
  rechtOder?: readonly (readonly [string, string])[];
  /**
   * Die Zielansicht wertet den row_scope NICHT aus (`permissions.require`) und
   * antwortet bei Scope EIGENE mit 403 — der Punkt darf dann gar nicht
   * erscheinen. Siehe `AuthService.darfAlle`.
   */
  nurAlle?: boolean;
}

/** Eine Navigationsgruppe mit den Punkten, die in ihr sichtbar sind. */
interface NavGruppe {
  id: string;
  titel: string;
  punkte: NavItem[];
}

/** Beschreibung einer Gruppe: Reihenfolge ihrer Pfade + Grundzustand. */
interface NavGruppeDef {
  id: string;
  titel: string;
  pfade: readonly string[];
  /**
   * Grundzustand: zugeklappt, solange der Nutzer nichts anderes gewählt hat.
   * Zugeklappt startet, was nicht zum täglichen Griff gehört — die Überschrift
   * bleibt sichtbar, die Karte des Systems also vollständig.
   */
  zuInitial?: boolean;
}

/**
 * Gruppierung der Navigation (Befund D1/D2): 24 flache Einträge ohne Ordnung
 * waren für den Disponenten eine Suchaufgabe.
 *
 * Bewusst als Pfad-Liste NEBEN `nav` statt als Feld IN `nav`: die Reihenfolge
 * innerhalb einer Gruppe steht damit an einer einzigen, lesbaren Stelle, und
 * die ausführlichen Begründungen an den Einträgen bleiben unangetastet.
 *
 * Jeder Pfad muss in genau einer Gruppe stehen. Was fehlt, landet sichtbar in
 * „Sonstiges" (siehe `sichtbareGruppen`) — verschwinden darf ein Punkt nie.
 */
const NAV_GRUPPEN: readonly NavGruppeDef[] = [
  // Der eigene Schreibtisch: womit der Tag anfängt, unabhängig von der Rolle.
  // „Mein Bereich" (Stempeluhr, Personalakte) steht hier und nicht bei Personal:
  // Personal ist Verwaltung FREMDER Akten, das hier ist die eigene.
  { id: 'tag', titel: 'Mein Tag', pfade: ['/uebersicht', '/aufgaben', '/mein-bereich'] },
  // Die Auftragskette in ihrer natürlichen Folge: Meldung kommt herein → wird
  // Auftrag → wird ggf. freigegeben → hängt ggf. an einem Projekt.
  {
    id: 'auftrag',
    titel: 'Aufträge',
    pfade: ['/eingang', '/auftraege', '/entscheidungen', '/projekte'],
  },
  // Disposition: wer wann wohin fährt. Wartung ist der wiederkehrende Teil
  // derselben Frage (Termine aus Verträgen statt aus Aufträgen).
  { id: 'dispo', titel: 'Disposition', pfade: ['/planung', '/wartung'] },
  // Der Geldweg, ebenfalls in Flussrichtung: Angebot/Rechnung raus,
  // Eingangsbeleg rein, Buchhaltung führt, Auswertung schaut zurück.
  {
    id: 'kfm',
    titel: 'Kaufmännisch',
    pfade: ['/dokumente', '/belegerfassung', '/buchhaltung', '/auswertungen'],
  },
  // Was gepflegt wird, nicht was passiert — Nachschlagewerke des Betriebs.
  {
    id: 'stamm',
    titel: 'Stammdaten',
    pfade: ['/kontakte', '/liegenschaften', '/artikel', '/geraetewissen'],
  },
  // Fremde Akten und Zeiten: Verwaltungsaufgabe weniger Rollen, deshalb
  // zugeklappt im Grundzustand.
  { id: 'personal', titel: 'Personal', pfade: ['/mitarbeiter', '/zeiterfassung'], zuInitial: true },
  // „KI + CRM, nicht CRM + KI" (CLAUDE.md): die KI ist ein eigener Akteur und
  // bekommt deshalb eine eigene Gruppe, keinen Anhang am Tagesgeschäft.
  { id: 'ki', titel: 'KI', pfade: ['/ki-assistent', '/ki-vorschlaege'], zuInitial: true },
  // Governance und Einrichtung: Vier-Augen-Freigaben hängen an keinem
  // Fachbereich (Bankdaten, Rechnungskorrektur) — sie sind Kontrolle über allen.
  {
    id: 'system',
    titel: 'System',
    pfade: ['/freigaben', '/werkzeuge', '/einstellungen'],
    zuInitial: true,
  },
];

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, Kommandopalette, Benachrichtigungen],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App {
  private readonly router = inject(Router);
  private readonly host = inject(ElementRef<HTMLElement>);
  private readonly injector = inject(Injector);
  protected readonly themeSvc = inject(ThemeService);
  protected readonly auth = inject(AuthService);

  // Rechte-Gates spiegeln die Server-Durchsetzung (permissions.py): das UI
  // blendet aus, was ohnehin mit 403 abgelehnt würde. Übersicht bleibt frei.
  //
  // Die Kennung („00", „12") ist die Bemaßung am Maßband und zählt von oben nach
  // unten durch. Sie folgt der GRUPPE: jede Gruppe beginnt bei einem vollen
  // Zehner, ihre Punkte gehen in Zweierschritten weiter. So bleibt Platz für
  // neue Punkte, ohne dass die Folgegruppen umnummeriert werden müssen.
  protected readonly nav: NavItem[] = [
    // ---- Mein Tag ----------------------------------------------------------
    { path: '/uebersicht', label: 'Übersicht', mark: '00' },
    { path: '/aufgaben', label: 'Aufgaben', mark: '02', recht: ['workflow', 'LESEN'] },
    // „Mein Bereich" ist der persönliche Bereich — Stempeluhr, Personalakte und
    // eigene Anträge. Für JEDEN, der Zeit erfassen darf, also gerade auch für
    // den Monteur mit Scope EIGENE.
    //
    // Das Recht ist hr/LESEN, nicht hr/AENDERN: Es ist das Minimum des
    // Bereichs (die Personalakte), nicht das seines ersten Reiters. Wer keine
    // eigene Zeit erfasst — etwa eine Bürokraft —, sieht den Punkt trotzdem,
    // landet über den Funktions-Redirect in seiner Akte, und der Reiter „Meine
    // Zeiten" bleibt ihm über `darfStempeln()` verborgen. Stünde hier
    // hr/AENDERN, sähe genau diese Person den Einstieg nicht und der
    // rechtsabhängige Redirect liefe ins Leere.
    { path: '/mein-bereich', label: 'Mein Bereich', mark: '04', recht: ['hr', 'LESEN'] },

    // ---- Aufträge ----------------------------------------------------------
    // Der Eingang (Vorgangs-Eingangskorb) ist der optionale Feeder vor dem
    // Auftrag: hier landen Meldungen und werden angenommen (→ Auftrag) oder
    // abgelehnt.
    { path: '/eingang', label: 'Eingang', mark: '10', recht: ['workflow', 'LESEN'] },
    // Der Auftrag ist das zentrale Arbeitsobjekt. `workflow/LESEN`
    // (scope-aware, wie /projekte), NICHT `nurAlle`: die Liste ist
    // scope-gefiltert, auch der Monteur sieht seine.
    { path: '/auftraege', label: 'Aufträge', mark: '12', recht: ['workflow', 'LESEN'] },
    // Vorgelegte Aufträge (FREIGABE_AUSSTEHEND) — der Schreibtisch der technischen
    // Leitung, direkt neben den Aufträgen, weil er ein Ausschnitt davon ist.
    // `workflow/FREIGEBEN`: Der Punkt gehört dem, der entscheiden kann; für alle
    // anderen wäre er eine Liste, an der sie nichts tun können.
    {
      path: '/entscheidungen',
      label: 'Auftragsfreigabe',
      mark: '14',
      recht: ['workflow', 'FREIGEBEN'],
    },
    // Begriffe an Hero angelehnt (Wiedererkennung): Projekte/Dokumente statt
    // Vorgänge/Belege — siehe docs/roadmap/00-informationsarchitektur.md.
    { path: '/projekte', label: 'Projekte', mark: '16', recht: ['workflow', 'LESEN'] },

    // ---- Disposition -------------------------------------------------------
    { path: '/planung', label: 'Planung', mark: '20', recht: ['workflow', 'LESEN'] },
    { path: '/wartung', label: 'Wartung', mark: '22', recht: ['maintenance', 'LESEN'] },

    // ---- Kaufmännisch ------------------------------------------------------
    // `/dokumente` bleibt ohne `nurAlle` — der Monteur bekommt dort die
    // preisfreie Angebotsliste (`features/angebot-mengen`).
    { path: '/dokumente', label: 'Dokumente', mark: '30', recht: ['invoicing', 'LESEN'] },
    // Eingangsrechnungen (accounting.receipt): eigener Belegkreis EB-, eigenes
    // Rechte-Modul — deshalb neben, nicht unter der Buchhaltung.
    { path: '/belegerfassung', label: 'Belegerfassung', mark: '32', recht: ['accounting', 'LESEN'] },
    // `nurAlle` (seit Migration 0102): MONTEUR trägt jetzt invoicing/LESEN mit Scope
    // EIGENE — er darf das ANGEBOT seines Objekts sehen (ohne Preise). Buchhaltung
    // und Auswertungen werten den Scope NICHT aus (`require` → 403) und sind
    // fachlich auch nichts für ihn: offene Posten, Mahnwesen, Umsatz, Marge. Ohne
    // dieses Flag stünden ihm beide Punkte in der Navigation und führten auf „Kein
    // Zugriff".
    {
      path: '/buchhaltung',
      label: 'Buchhaltung',
      mark: '34',
      recht: ['invoicing', 'LESEN'],
      nurAlle: true,
    },
    {
      path: '/auswertungen',
      label: 'Auswertungen',
      mark: '36',
      recht: ['invoicing', 'LESEN'],
      nurAlle: true,
    },

    // ---- Stammdaten --------------------------------------------------------
    { path: '/kontakte', label: 'Kontakte', mark: '40', recht: ['identity', 'LESEN'] },
    { path: '/liegenschaften', label: 'Liegenschaften', mark: '42', recht: ['property', 'LESEN'] },
    { path: '/artikel', label: 'Artikel', mark: '44', recht: ['pricing', 'LESEN'] },
    // Gerätewissen: read-only-Sicht auf Hersteller-Ersatzteile (Vaillant/Junkers)
    // — dieselbe pricing/LESEN-Berechtigung wie der Artikelstamm, direkt daneben.
    { path: '/geraetewissen', label: 'Gerätewissen', mark: '46', recht: ['pricing', 'LESEN'] },

    // ---- Personal ----------------------------------------------------------
    // `nurAlle`: seit Migration 0068 trägt MONTEUR hr/LESEN mit Scope EIGENE
    // (für die eigene Zeiterfassung). Die Personalliste und die Verwaltungs-
    // sicht der Zeiterfassung werten den Scope nicht aus und antworten mit 403
    // — sie dürfen ihm deshalb gar nicht erst angeboten werden. Sein Einstieg
    // ist „Mein Bereich" (00er-Block).
    { path: '/mitarbeiter', label: 'Mitarbeiter', mark: '50', recht: ['hr', 'LESEN'], nurAlle: true },
    // Zeiterfassung (Verwaltung): Arbeitstage prüfen, bestätigen, exportieren.
    // Gesetzlicher Kern: § 17 MiLoG (Beginn/Ende/Dauer, 7 Tage, 2 Jahre, Zoll).
    {
      path: '/zeiterfassung',
      label: 'Zeiterfassung',
      mark: '52',
      recht: ['hr', 'LESEN'],
      nurAlle: true,
    },

    // ---- KI ----------------------------------------------------------------
    // KI-Assistent („frag das CRM"): konversationelle Auskunft, serverseitig
    // gegroundet in Suche + Dossier mit den Rechten des Anmelders. KEIN `nurAlle`:
    // die Antwort ist rechte-/objektsicht-gefiltert, also auch für Scope EIGENE
    // sinnvoll (der Monteur fragt nach seinen Objekten).
    { path: '/ki-assistent', label: 'KI-Assistent', mark: '60', recht: ['workflow', 'LESEN'] },
    // KI-Vorschläge (ai_proposal): die Review-Queue der KI — schlägt vor, der
    // Mensch nimmt an. `nurAlle`: die Liste steht auf `require` (fail-closed), ein
    // Konto mit row_scope EIGENE bekommt 403.
    {
      path: '/ki-vorschlaege',
      label: 'KI-Vorschläge',
      mark: '62',
      recht: ['workflow', 'LESEN'],
      nurAlle: true,
    },

    // ---- System ------------------------------------------------------------
    // Vier-Augen-Freigaben sind bereichsübergreifende Governance (Bankdaten,
    // Rechnungskorrektur) — sie hängen an keinem Fachbereich.
    { path: '/freigaben', label: 'Vier-Augen-Freigaben', mark: '70', recht: ['security', 'LESEN'] },
    // Werkzeuge (Heizlast, Heizkörper, Volumenstrom, Einheiten): reine Rechner
    // ohne Serverzugriff — kein Modulrecht, für jede angemeldete Rolle sichtbar.
    { path: '/werkzeuge', label: 'Werkzeuge', mark: '72' },
    // Einstellungen: nur für Rollen, die etwas ändern dürfen (Firmenprofil/
    // Gewerke/Niederlassungen = company/AENDERN, Mahnstufen = invoicing/AENDERN).
    {
      path: '/einstellungen',
      label: 'Einstellungen',
      mark: '74',
      rechtOder: [
        ['company', 'AENDERN'],
        ['invoicing', 'AENDERN'],
        // Rechtematrix/Rollenpflege liegt als Unterseite unter Einstellungen.
        ['security', 'AENDERN'],
      ],
    },
  ];

  /**
   * Header-CTA „Meldung erfassen" — der Schnelleinstieg legt Person +
   * Liegenschaft + Vorgang atomar an. Nur zeigen, wenn ALLE vier beteiligten
   * Tore vorliegen (der Server würde sonst mit 403 abbrechen und alles
   * zurückrollen). Spiegelt die Server-Durchsetzung, setzt sie nicht durch.
   */
  /**
   * Header-CTA „＋ Neuer Auftrag" — der globale Schnelleinstieg legt jetzt einen
   * Auftrag an (nicht mehr nur einen Vorgang). `darfAlle`, nicht `darf`:
   * `POST /api/workflow/work_orders` ist fail-closed (`permissions.require`) —
   * ein Konto mit row_scope EIGENE bekommt 403. Der CTA springt auf die
   * Auftragsliste und öffnet dort den Anlage-Dialog (Query `neu=1`).
   */
  protected readonly darfNeuerAuftrag = computed(() =>
    this.auth.darfAlle('workflow', 'ANLEGEN'),
  );

  protected readonly darfSchnellerfassung = computed(
    () =>
      // `darfAlle`: `quick-intake` ist an allen vier Toren fail-closed (`require`)
      // — ein Konto mit row_scope EIGENE bekommt 403, obwohl es die Rechte trägt.
      this.auth.darfAlle('identity', 'ANLEGEN') &&
      this.auth.darfAlle('property', 'ANLEGEN') &&
      this.auth.darfAlle('property', 'AENDERN') &&
      this.auth.darfAlle('workflow', 'ANLEGEN'),
  );

  /** Nur Navigationspunkte, für die (mindestens) ein Recht vorliegt. */
  protected readonly sichtbareNav = computed(() =>
    this.nav.filter((n) => {
      if (n.recht) {
        const ok = n.nurAlle
          ? this.auth.darfAlle(n.recht[0], n.recht[1])
          : this.auth.darf(n.recht[0], n.recht[1]);
        if (!ok) return false;
      }
      if (n.rechtOder && !n.rechtOder.some((r) => this.auth.darf(r[0], r[1]))) return false;
      return true;
    }),
  );

  /**
   * Die sichtbaren Punkte, in Gruppen einsortiert. Leere Gruppen (alle Punkte
   * wegen fehlender Rechte gefiltert) fallen weg — eine Überschrift ohne
   * Inhalt wäre nur Rauschen.
   */
  protected readonly sichtbareGruppen = computed<NavGruppe[]>(() => {
    const punkte = this.sichtbareNav();
    const vergeben = new Set<string>();
    const gruppen: NavGruppe[] = [];

    for (const g of NAV_GRUPPEN) {
      // Reihenfolge kommt aus `pfade`, nicht aus `nav` — die Gruppe bestimmt,
      // in welcher Folge ihre Punkte stehen.
      // Stünde ein Pfad versehentlich doppelt, erschiene er zweimal und die
      // Bemaszungsmarke zählte eine Zeile zu viel — ab dort stünde sie
      // dauerhaft daneben. `new Set` faengt die Dublette INNERHALB dieser
      // Gruppe ab, `vergeben` die ueber Gruppen hinweg. Die erste Nennung
      // gewinnt; jede weitere faellt still weg, statt die Navigation zu
      // verstellen.
      const treffer = [...new Set(g.pfade)]
        .filter((p) => !vergeben.has(p))
        .map((p) => punkte.find((n) => n.path === p))
        .filter((n): n is NavItem => !!n);
      treffer.forEach((n) => vergeben.add(n.path));
      if (treffer.length) gruppen.push({ id: g.id, titel: g.titel, punkte: treffer });
    }

    // Sicherheitsnetz: ein neuer Navigationspunkt, den niemand einer Gruppe
    // zugeordnet hat, wird sichtbar angehängt statt still verschluckt.
    const rest = punkte.filter((n) => !vergeben.has(n.path));
    if (rest.length) gruppen.push({ id: 'sonstiges', titel: 'Sonstiges', punkte: rest });

    return gruppen;
  });

  /** Aktuelle URL — Grundlage für die aktive Bemaszungsmarke. */
  private readonly aktuelleUrl = signal('/');

  // ------------------------- Einklappbare Gruppen ----------------------------
  /**
   * Zugeklappte Gruppen (IDs). Die Wahl wird im Browser gemerkt: Wer nie
   * Buchhaltung macht, soll die Liste nicht bei jedem Besuch neu wegräumen.
   *
   * Zugeklappt heißt NICHT versteckt: die Überschrift bleibt stehen, die Karte
   * des Systems ist also weiter vollständig lesbar.
   */
  private readonly zugeklappt = signal<ReadonlySet<string>>(this.zuLaden());

  private zuLaden(): ReadonlySet<string> {
    const grund = new Set(NAV_GRUPPEN.filter((g) => g.zuInitial).map((g) => g.id));
    try {
      const roh = localStorage.getItem(NAV_ZU_SPEICHER);
      if (roh === null) return grund; // noch nie gewählt → Grundzustand
      const ids: unknown = JSON.parse(roh);
      if (!Array.isArray(ids)) return grund;
      return new Set(ids.filter((x): x is string => typeof x === 'string'));
    } catch {
      // Gesperrter oder beschädigter Speicher darf die Navigation nicht kosten.
      return grund;
    }
  }

  private zuSpeichern(ids: ReadonlySet<string>): void {
    try {
      localStorage.setItem(NAV_ZU_SPEICHER, JSON.stringify([...ids]));
    } catch {
      // s. o. — die Wahl gilt dann nur für diese Sitzung.
    }
  }

  /** Ist die Gruppe aufgeklappt? */
  protected offen(id: string): boolean {
    return !this.zugeklappt().has(id);
  }

  protected gruppeUmschalten(id: string): void {
    const naechste = new Set(this.zugeklappt());
    if (!naechste.delete(id)) naechste.add(id);
    this.zugeklappt.set(naechste);
    this.zuSpeichern(naechste);
  }

  /** Gruppe, in der die aktuell offene Seite liegt (für die Markierung). */
  protected readonly aktiveGruppe = computed<string | null>(() => {
    const url = this.aktuelleUrl();
    for (const g of this.sichtbareGruppen()) {
      for (const n of g.punkte) {
        if (url.startsWith(n.path)) return g.id;
      }
    }
    return null;
  });

  /**
   * Beim Seitenwechsel die Gruppe der Zielseite aufklappen — wer über Suche,
   * Glocke oder einen Link irgendwo landet, soll sich in der Navigation
   * wiederfinden. Läuft NUR bei Navigation, nicht bei jeder Zustandsänderung:
   * sonst spränge eine gerade zugeklappte Gruppe sofort wieder auf.
   */
  private aktiveGruppeAufklappen(): void {
    const id = this.aktiveGruppe();
    if (!id || this.offen(id)) return;
    const naechste = new Set(this.zugeklappt());
    naechste.delete(id);
    this.zugeklappt.set(naechste);
    this.zuSpeichern(naechste);
  }

  /**
   * Position des aktiven Punkts als Zeilenversatz: wie viele Punkte und wie
   * viele Gruppenüberschriften stehen über ihm. Die Bemaszungsmarke rechnet
   * daraus ihren Weg (`punkte × --nav-item-h + koepfe × --nav-head-h`) —
   * seit der Gruppierung genügt der reine Index nicht mehr.
   *
   * Zugeklappte Gruppen rendern KEINE Zeilen; ihre Punkte dürfen deshalb auch
   * nicht mitgezählt werden, sonst stünde die Marke ab dort dauerhaft daneben.
   */
  private readonly aktivePos = computed(() => {
    const url = this.aktuelleUrl();
    const zu = this.zugeklappt();
    let punkte = 0;
    let koepfe = 0;
    for (const g of this.sichtbareGruppen()) {
      koepfe++; // die Überschrift dieser Gruppe steht über allen ihren Punkten
      if (zu.has(g.id)) continue;
      for (const n of g.punkte) {
        if (url.startsWith(n.path)) return { punkte, koepfe, sichtbar: true };
        punkte++;
      }
    }
    // Kein sichtbarer Treffer (zugeklappte Gruppe oder /login): Die Marke ruht
    // auf dem ersten Punkt und wird ausgeblendet — sie darf nicht auf eine
    // Zeile zeigen, die gar nicht die offene Seite ist.
    return { punkte: 0, koepfe: 1, sichtbar: false };
  });

  protected readonly activeIndex = computed(() => this.aktivePos().punkte);
  protected readonly activeHeads = computed(() => this.aktivePos().koepfe);
  protected readonly markeSichtbar = computed(() => this.aktivePos().sichtbar);

  protected readonly rollenText = computed(() => {
    const rollen = this.auth.user()?.roles ?? [];
    return rollen.length ? rollen.join(' · ') : 'Ohne Rolle';
  });

  protected readonly themeLabel = computed(() =>
    this.themeSvc.theme() === 'dark' ? 'Zu hellem Design wechseln' : 'Zu dunklem Design wechseln',
  );

  /**
   * Eingeklappte Navigation: nur die Bemaßungskennungen, kein Text.
   *
   * Für die Plantafel — das Hauptwerkzeug des Disponenten — zählt jede Spalte
   * Breite: Eingeklappt gibt die Navigation gut 11 rem an das Board zurück, und
   * eine ganze Woche passt eher in den Schirm. Die Wahl wird gemerkt.
   *
   * Die Beschriftungen bleiben dabei IM DOM (nur optisch abgeschnitten) — ein
   * Link, dessen zugänglicher Name auf „50" zusammenschrumpft, wäre für
   * Screenreader wertlos.
   */
  protected readonly navSchmal = signal(this.navSchmalLaden());

  private navSchmalLaden(): boolean {
    try {
      return localStorage.getItem(NAV_SPEICHER) === '1';
    } catch {
      return false;
    }
  }

  navUmschalten(): void {
    const schmal = !this.navSchmal();
    this.navSchmal.set(schmal);
    try {
      localStorage.setItem(NAV_SPEICHER, schmal ? '1' : '0');
    } catch {
      // Ein gesperrter Speicher darf die Navigation nicht kosten.
    }
  }

  /**
   * Den aktiven Punkt in den sichtbaren Teil der Navigation holen.
   *
   * Nur `scrollTop` der Leiste wird angefasst — `scrollIntoView` würde bei
   * Bedarf auch die Seite darunter verschieben, und ein Seitenwechsel darf den
   * Inhalt nicht wegscrollen.
   */
  private aktivenPunktZeigen(): void {
    const el = this.host.nativeElement as HTMLElement;
    const nav = el.querySelector<HTMLElement>('.nav');
    const punkt = el.querySelector<HTMLElement>('.nav__link[aria-current="page"]');
    if (!nav || !punkt) return;
    const oben = punkt.offsetTop; // relativ zu .nav__scroll = Scroll-Nullpunkt
    const hoehe = punkt.offsetHeight;
    if (oben >= nav.scrollTop && oben + hoehe <= nav.scrollTop + nav.clientHeight) return;
    nav.scrollTop = Math.max(0, oben - (nav.clientHeight - hoehe) / 2);
  }

  constructor() {
    // Nach jedem Seitenwechsel und nach jedem Auf-/Zuklappen nachfassen: erst
    // wenn gerendert ist, steht `aria-current` an der richtigen Zeile.
    effect(() => {
      this.aktuelleUrl();
      this.zugeklappt();
      this.navSchmal();
      afterNextRender(() => this.aktivenPunktZeigen(), { injector: this.injector });
    });

    this.themeSvc.init();
    this.aktuelleUrl.set(this.router.url);
    this.aktiveGruppeAufklappen();
    this.router.events
      .pipe(filter((e): e is NavigationEnd => e instanceof NavigationEnd))
      .subscribe((e) => {
        this.aktuelleUrl.set(e.urlAfterRedirects);
        this.aktiveGruppeAufklappen();
      });
  }

  abmelden(): void {
    this.auth.abmelden().subscribe({
      next: () => this.router.navigate(['/login']),
      error: () => this.router.navigate(['/login']),
    });
  }
}
