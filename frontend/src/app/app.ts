import { Component, computed, inject, signal } from '@angular/core';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs';
import { ThemeService } from './core/theme';
import { AuthService } from './core/auth.service';
import { Kommandopalette } from './shared/kommandopalette/kommandopalette';

const NAV_SPEICHER = 'mcn.nav.schmal';

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
const NAV_GRUPPEN: readonly { id: string; titel: string; pfade: readonly string[] }[] = [
  {
    id: 'tag',
    titel: 'Tagesgeschäft',
    pfade: ['/uebersicht', '/auftraege', '/eingang', '/projekte', '/planung', '/wartung', '/aufgaben'],
  },
  // Entscheidungs-Schreibtische: beide sind Warteschlangen, an denen jemand
  // zustimmt oder ablehnt — fachlich dasselbe Tun, unabhängig vom Bereich.
  { id: 'freigaben', titel: 'Freigaben', pfade: ['/entscheidungen', '/freigaben'] },
  // „KI + CRM, nicht CRM + KI" (CLAUDE.md): die KI ist ein eigener Akteur und
  // bekommt deshalb eine eigene Gruppe, keinen Anhang am Tagesgeschäft.
  { id: 'ki', titel: 'KI', pfade: ['/ki-vorschlaege', '/ki-assistent'] },
  {
    id: 'stamm',
    titel: 'Stammdaten',
    pfade: ['/kontakte', '/liegenschaften', '/artikel', '/geraetewissen'],
  },
  {
    id: 'kfm',
    titel: 'Kaufmännisch',
    pfade: ['/dokumente', '/buchhaltung', '/belegerfassung', '/auswertungen'],
  },
  { id: 'personal', titel: 'Personal', pfade: ['/mitarbeiter', '/zeiterfassung', '/meine-zeiten'] },
  { id: 'system', titel: 'System', pfade: ['/werkzeuge', '/einstellungen'] },
];

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, Kommandopalette],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App {
  private readonly router = inject(Router);
  protected readonly themeSvc = inject(ThemeService);
  protected readonly auth = inject(AuthService);

  // Rechte-Gates spiegeln die Server-Durchsetzung (permissions.py): das UI
  // blendet aus, was ohnehin mit 403 abgelehnt würde. Übersicht bleibt frei.
  protected readonly nav: NavItem[] = [
    { path: '/uebersicht', label: 'Übersicht', mark: '00' },
    // Der Auftrag ist das zentrale Arbeitsobjekt — prominent ganz oben, direkt
    // nach der Übersicht. `workflow/LESEN` (scope-aware, wie /projekte), NICHT
    // `nurAlle`: die Liste ist scope-gefiltert, auch der Monteur sieht seine.
    { path: '/auftraege', label: 'Aufträge', mark: '05', recht: ['workflow', 'LESEN'] },
    // Vorgelegte Aufträge (FREIGABE_AUSSTEHEND) — der Schreibtisch der technischen
    // Leitung, direkt neben den Aufträgen, weil er ein Ausschnitt davon ist.
    // `workflow/FREIGEBEN`: Der Punkt gehört dem, der entscheiden kann; für alle
    // anderen wäre er eine Liste, an der sie nichts tun können. Zwischenschritt 06
    // statt Renummerierung der Folgepunkte.
    {
      path: '/entscheidungen',
      label: 'Auftragsfreigabe',
      mark: '06',
      recht: ['workflow', 'FREIGEBEN'],
    },
    // Der Eingang (Vorgangs-Eingangskorb) ist der optionale Feeder direkt unter
    // den Aufträgen: hier landen Meldungen und werden angenommen (→ Auftrag) oder
    // abgelehnt.
    { path: '/eingang', label: 'Eingang', mark: '08', recht: ['workflow', 'LESEN'] },
    { path: '/kontakte', label: 'Kontakte', mark: '10', recht: ['identity', 'LESEN'] },
    { path: '/liegenschaften', label: 'Liegenschaften', mark: '20', recht: ['property', 'LESEN'] },
    // Begriffe an Hero angelehnt (Wiedererkennung): Projekte/Dokumente statt
    // Vorgänge/Belege — siehe docs/roadmap/00-informationsarchitektur.md.
    { path: '/projekte', label: 'Projekte', mark: '30', recht: ['workflow', 'LESEN'] },
    { path: '/dokumente', label: 'Dokumente', mark: '40', recht: ['invoicing', 'LESEN'] },
    { path: '/planung', label: 'Planung', mark: '50', recht: ['workflow', 'LESEN'] },
    // Wartung liegt fachlich beim Service-/Einsatz-Cluster (wiederkehrende
    // Einsätze) → Zwischenschritt 55 statt Renummerierung der Folgepunkte.
    { path: '/wartung', label: 'Wartung', mark: '55', recht: ['maintenance', 'LESEN'] },
    { path: '/aufgaben', label: 'Aufgaben', mark: '60', recht: ['workflow', 'LESEN'] },
    // Vier-Augen-Freigaben sind bereichsübergreifende Governance (Bankdaten,
    // Rechnungskorrektur) — sie hängen an keinem Fachbereich, stehen aber bei
    // der Arbeitsorganisation. Zwischenschritt 62 statt Renummerierung.
    { path: '/freigaben', label: 'Vier-Augen-Freigaben', mark: '62', recht: ['security', 'LESEN'] },
    // KI-Vorschläge (ai_proposal): die Review-Queue der KI — schlägt vor, der
    // Mensch nimmt an. `nurAlle`: die Liste steht auf `require` (fail-closed), ein
    // Konto mit row_scope EIGENE bekommt 403. Neben den Freigaben (Governance).
    {
      path: '/ki-vorschlaege',
      label: 'KI-Vorschläge',
      mark: '63',
      recht: ['workflow', 'LESEN'],
      nurAlle: true,
    },
    // KI-Assistent („frag das CRM"): konversationelle Auskunft, serverseitig
    // gegroundet in Suche + Dossier mit den Rechten des Anmelders. KEIN `nurAlle`:
    // die Antwort ist rechte-/objektsicht-gefiltert, also auch für Scope EIGENE
    // sinnvoll (der Monteur fragt nach seinen Objekten).
    { path: '/ki-assistent', label: 'KI-Assistent', mark: '64', recht: ['workflow', 'LESEN'] },
    // Personal/HR liegt fachlich zwischen interner Arbeitsorganisation (Aufgaben)
    // und dem Stammdaten-Cluster (Artikel) → Zwischenschritt 65 statt
    // Renummerierung der Folgepunkte.
    // `nurAlle`: seit Migration 0068 trägt MONTEUR hr/LESEN mit Scope EIGENE
    // (für die eigene Zeiterfassung). Die Personalliste und die Verwaltungs-
    // sicht der Zeiterfassung werten den Scope nicht aus und antworten mit 403
    // — sie dürfen ihm deshalb gar nicht erst angeboten werden.
    { path: '/mitarbeiter', label: 'Mitarbeiter', mark: '65', recht: ['hr', 'LESEN'], nurAlle: true },
    // Zeiterfassung (Verwaltung): Arbeitstage prüfen, bestätigen, exportieren.
    // Gesetzlicher Kern: § 17 MiLoG (Beginn/Ende/Dauer, 7 Tage, 2 Jahre, Zoll).
    {
      path: '/zeiterfassung',
      label: 'Zeiterfassung',
      mark: '66',
      recht: ['hr', 'LESEN'],
      nurAlle: true,
    },
    // „Meine Zeiten" ist die Stempeluhr — für JEDEN, der Zeit erfassen darf,
    // also gerade auch für den Monteur mit Scope EIGENE.
    { path: '/meine-zeiten', label: 'Meine Zeiten', mark: '67', recht: ['hr', 'AENDERN'] },
    { path: '/artikel', label: 'Artikel', mark: '70', recht: ['pricing', 'LESEN'] },
    // Gerätewissen: read-only-Sicht auf Hersteller-Ersatzteile (Vaillant/Junkers)
    // — dieselbe pricing/LESEN-Berechtigung wie der Artikelstamm, direkt daneben.
    { path: '/geraetewissen', label: 'Gerätewissen', mark: '72', recht: ['pricing', 'LESEN'] },
    // `nurAlle` (seit Migration 0102): MONTEUR trägt jetzt invoicing/LESEN mit Scope
    // EIGENE — er darf das ANGEBOT seines Objekts sehen (ohne Preise). Buchhaltung
    // und Auswertungen werten den Scope NICHT aus (`require` → 403) und sind
    // fachlich auch nichts für ihn: offene Posten, Mahnwesen, Umsatz, Marge. Ohne
    // dieses Flag stünden ihm beide Punkte in der Navigation und führten auf „Kein
    // Zugriff". `/dokumente` bleibt ohne Flag — dort bekommt er die preisfreie
    // Angebotsliste (`features/angebot-mengen`).
    {
      path: '/buchhaltung',
      label: 'Buchhaltung',
      mark: '80',
      recht: ['invoicing', 'LESEN'],
      nurAlle: true,
    },
    // Eingangsrechnungen (accounting.receipt): eigener Belegkreis EB-, eigenes
    // Rechte-Modul — deshalb neben, nicht unter der Buchhaltung.
    { path: '/belegerfassung', label: 'Belegerfassung', mark: '82', recht: ['accounting', 'LESEN'] },
    {
      path: '/auswertungen',
      label: 'Auswertungen',
      mark: '90',
      recht: ['invoicing', 'LESEN'],
      nurAlle: true,
    },
    // Werkzeuge (Heizlast, Heizkörper, Volumenstrom, Einheiten): reine Rechner
    // ohne Serverzugriff — kein Modulrecht, für jede angemeldete Rolle sichtbar.
    { path: '/werkzeuge', label: 'Werkzeuge', mark: '92' },
    // Einstellungen: nur für Rollen, die etwas ändern dürfen (Firmenprofil/
    // Gewerke/Niederlassungen = company/AENDERN, Mahnstufen = invoicing/AENDERN).
    {
      path: '/einstellungen',
      label: 'Einstellungen',
      mark: '95',
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

  /**
   * Position des aktiven Punkts als Zeilenversatz: wie viele Punkte und wie
   * viele Gruppenüberschriften stehen über ihm. Die Bemaszungsmarke rechnet
   * daraus ihren Weg (`punkte × --nav-item-h + koepfe × --nav-head-h`) —
   * seit der Gruppierung genügt der reine Index nicht mehr.
   */
  private readonly aktivePos = computed(() => {
    const url = this.aktuelleUrl();
    let punkte = 0;
    let koepfe = 0;
    for (const g of this.sichtbareGruppen()) {
      koepfe++; // die Überschrift dieser Gruppe steht über allen ihren Punkten
      for (const n of g.punkte) {
        if (url.startsWith(n.path)) return { punkte, koepfe };
        punkte++;
      }
    }
    // Kein Treffer (z. B. /login): Marke ruht auf dem ersten Punkt.
    return { punkte: 0, koepfe: 1 };
  });

  protected readonly activeIndex = computed(() => this.aktivePos().punkte);
  protected readonly activeHeads = computed(() => this.aktivePos().koepfe);

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

  constructor() {
    this.themeSvc.init();
    this.aktuelleUrl.set(this.router.url);
    this.router.events
      .pipe(filter((e): e is NavigationEnd => e instanceof NavigationEnd))
      .subscribe((e) => this.aktuelleUrl.set(e.urlAfterRedirects));
  }

  abmelden(): void {
    this.auth.abmelden().subscribe({
      next: () => this.router.navigate(['/login']),
      error: () => this.router.navigate(['/login']),
    });
  }
}
