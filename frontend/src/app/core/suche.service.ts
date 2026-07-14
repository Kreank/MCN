import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

/** Entitaetsarten, die die globale Suche zurueckgeben kann. */
export type SucheEntityTyp =
  | 'KONTAKT'
  | 'LIEGENSCHAFT'
  | 'PROJEKT'
  | 'VORGANG'
  | 'AUFTRAG'
  | 'EINSATZ'
  | 'ANGEBOT'
  | 'RECHNUNG'
  | 'ARTIKEL'
  | 'LEISTUNG'
  | 'MITARBEITER';

/**
 * Ein Treffer der globalen Suche (1:1 `TrefferOut`, backend/api/suche.py).
 *
 * `typ` ist serverseitig ein freies `str`. Die Union ist die Bequemlichkeit fuer
 * die Abbildungstabellen; eine unbekannte Art faellt nicht durch, sondern wird
 * defensiv behandelt (eigene Gruppe am Ende, kein Sprungziel).
 */
export interface SucheTreffer {
  typ: SucheEntityTyp;
  id: string;
  /** Anzeigename, z. B. „Badensche Straße 53". */
  titel: string;
  /** Kontextzeile, z. B. „AU-2026-000012 · Badensche Straße 53 · IN_ARBEIT". */
  untertitel: string;
  status: string | null;
  /** 0 = Kennung exakt · 1 = Wortanfang · 2 = Teilstring · 3 = ueber eine Beziehung. */
  rang: number;
  /** Warum getroffen, z. B. „Adresse der Liegenschaft". Nie leer/null. */
  grund: string;
  ist_direkttreffer: boolean;
}

/**
 * Zaehlwerk je Kategorie (`KategorieOut`).
 *
 * `anzahl` ist die Zahl der TATSAECHLICH gelieferten Zeilen. `mehr_vorhanden`
 * heisst: es gaebe mehr. Der Sonderfall `anzahl = 0` bei `mehr_vorhanden = true`
 * ist echt — dann hat die Gesamtgrenze die Kategorie ganz aus der Liste gedraengt.
 * Beides muss das UI sagen; stilles Abschneiden ist genau das, was der Nutzer am
 * alten System hasst.
 */
export interface SucheKategorie {
  typ: SucheEntityTyp;
  anzahl: number;
  mehr_vorhanden: boolean;
}

/** Antwort der globalen Suche (`SucheOut`). */
export interface SucheErgebnis {
  /** Der gesuchte Begriff, wie der Server ihn angenommen hat: getrimmt und auf
   * 200 Zeichen gekappt — sonst unveraendert (keine Normalisierung). */
  begriff: string;
  /** Flach und ranggeordnet — der Direkttreffer steht hier zusaetzlich drin. */
  treffer: SucheTreffer[];
  /** Genau eine exakte Kennung erkannt -> Enter springt sofort dorthin. */
  direkttreffer: SucheTreffer | null;
  kategorien: SucheKategorie[];
}

/**
 * Zielroute je Entitaetsart (Basis-Segment; die ID wird angehaengt).
 *
 * Die Rechnung fuehrt auf `/rechnungen/:id` — das ist die Belegansicht, auf die
 * auch die Dokumentenliste verlinkt. `/buchhaltung/:id` ist die Zahlungssicht
 * derselben Rechnung und damit hier das falsche Ziel.
 */
export const SUCHE_ZIEL: Readonly<Record<SucheEntityTyp, string>> = {
  KONTAKT: '/kontakte',
  LIEGENSCHAFT: '/liegenschaften',
  PROJEKT: '/projekte',
  VORGANG: '/vorgaenge',
  AUFTRAG: '/auftraege',
  EINSATZ: '/planung',
  ANGEBOT: '/dokumente',
  RECHNUNG: '/rechnungen',
  ARTIKEL: '/artikel',
  LEISTUNG: '/leistungen',
  MITARBEITER: '/mitarbeiter',
};

/** Ueberschrift der Ergebnisgruppe je Entitaetsart (Plural). */
export const SUCHE_KATEGORIE: Readonly<Record<SucheEntityTyp, string>> = {
  KONTAKT: 'Kontakte',
  LIEGENSCHAFT: 'Liegenschaften',
  PROJEKT: 'Projekte',
  VORGANG: 'Vorgänge',
  AUFTRAG: 'Aufträge',
  EINSATZ: 'Einsätze',
  ANGEBOT: 'Angebote',
  RECHNUNG: 'Rechnungen',
  ARTIKEL: 'Artikel',
  LEISTUNG: 'Leistungen',
  MITARBEITER: 'Mitarbeiter',
};

/** Kurzkennung der Kategorie (Mono-Marke, wie die Messkante der Navigation). */
export const SUCHE_MARK: Readonly<Record<SucheEntityTyp, string>> = {
  KONTAKT: '10',
  LIEGENSCHAFT: '20',
  PROJEKT: '30',
  VORGANG: '31',
  AUFTRAG: '32',
  EINSATZ: '50',
  ANGEBOT: '40',
  RECHNUNG: '41',
  MITARBEITER: '65',
  ARTIKEL: '70',
  LEISTUNG: '71',
};

/**
 * Anzeigereihenfolge der Kategorien. Fachlich absteigend nach „woran arbeite
 * ich gerade": erst die Objekte des Tagesgeschaefts, dann die Stammdaten.
 * Arten, die der Server (spaeter) zusaetzlich liefert, haengen hinten an.
 */
export const SUCHE_ORDNUNG: readonly SucheEntityTyp[] = [
  'KONTAKT',
  'LIEGENSCHAFT',
  'PROJEKT',
  'VORGANG',
  'AUFTRAG',
  'EINSATZ',
  'ANGEBOT',
  'RECHNUNG',
  'ARTIKEL',
  'LEISTUNG',
  'MITARBEITER',
];

// ---------------------------------------------------------------------------
// Spiegel der Serverregeln (db_core/services/suche.py)
// ---------------------------------------------------------------------------
// Der Server schneidet an zwei Stellen still ab. Damit das UI das EHRLICH sagen
// kann (und nicht „keine Treffer" luegt, wo gar nicht gesucht wurde), muss es
// dieselbe Regel rechnen. Weicht sie ab, zeigt das UI den Hinweis genau falsch
// herum — deshalb steht sie hier gebuendelt und nicht verstreut im Template.

/** `UMLAUTE` + `normalisieren()` (suche.py:180, 219). */
const UMLAUTE: readonly (readonly [RegExp, string])[] = [
  [/ä/g, 'ae'],
  [/ö/g, 'oe'],
  [/ü/g, 'ue'],
  [/ß/g, 'ss'],
];

/** Kleinschreibung, Umlaute/ß entfaltet, alles Nicht-Alphanumerische raus. */
export function normalisieren(text: string): string {
  let t = (text ?? '').toLowerCase();
  for (const [muster, ersatz] of UMLAUTE) t = t.replace(muster, ersatz);
  return t.replace(/[^a-z0-9]/g, '');
}

/** `MAX_TOKENS` (suche.py:173). */
const MAX_TOKENS = 8;

/** Begriff → normalisierte Tokens (`tokenisieren()`, suche.py:276). */
export function tokenisieren(begriff: string): string[] {
  return (begriff ?? '')
    .split(/\s+/)
    .map(normalisieren)
    .filter((t) => t.length > 0)
    .slice(0, MAX_TOKENS);
}

/** `MIN_LAENGE` (suche.py:166): darunter sucht der Server ueberhaupt nicht. */
export const SUCHE_MIN_LAENGE = 2;

/**
 * `TRIGRAMM_MIN` (suche.py:1246): Ein Trigramm braucht drei Zeichen. Darunter
 * bleibt **allein der Artikelstamm** aussen vor (die einzige Kategorie mit
 * Hunderttausenden Zeilen). Leistungen, Belege, Kontakte werden weiter gesucht —
 * ihr Zweig kennt die Grenze nicht.
 */
export const SUCHE_TRIGRAMM_MIN = 3;

/** `HERO_OPERATOREN` (suche.py:1236): heben die Trigramm-Grenze auf. */
const HERO_OPERATOREN = ['+', '|', '*'];

/** Laengstes Token — die Groesse, an der beide Serverschwellen haengen. */
function laengstesToken(begriff: string): number {
  const tokens = tokenisieren(begriff);
  return tokens.length ? Math.max(...tokens.map((t) => t.length)) : 0;
}

/** Der Server sucht bei diesem Begriff GAR NICHT (`suche.py:1356`). */
export function begriffZuKurz(begriff: string): boolean {
  const n = laengstesToken(begriff);
  return n > 0 && n < SUCHE_MIN_LAENGE;
}

/**
 * Der Server laesst den ARTIKELSTAMM aus — kein Token traegt drei Zeichen und
 * kein Hero-Operator hebt die Grenze auf (`suche.py:1275`, `:1282`).
 *
 * „ZR-6" wird zu „zr6" und ist damit lang genug; „ZR" nicht.
 */
export function artikelUebersprungen(begriff: string): boolean {
  if (HERO_OPERATOREN.some((op) => (begriff ?? '').includes(op))) return false;
  const n = laengstesToken(begriff);
  return n >= SUCHE_MIN_LAENGE && n < SUCHE_TRIGRAMM_MIN;
}

/**
 * Globale Suche (Kommandopalette).
 *
 * Der Server liefert ausschliesslich, was der angemeldete Nutzer sehen darf —
 * die Palette filtert nichts nach und blendet nichts ein, was nicht kam.
 */
@Injectable({ providedIn: 'root' })
export class SucheService {
  private readonly http = inject(HttpClient);

  suchen(q: string): Observable<SucheErgebnis> {
    const params = new HttpParams().set('q', q);
    return this.http.get<SucheErgebnis>('/api/suche', { params });
  }
}
