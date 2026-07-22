/**
 * Eigentum an einer Einheit (`tenure.ownership_period` + `ownership_interest`).
 *
 * Sascha in seiner Domänenmodell-Skizze: *„Es kann also sein, dass ich 20
 * Rechnungsadressen habe, die ich immer angeben muss, wenn ich Dokumente dazu
 * erzeuge."* Genau dafür gibt es diesen Slice.
 *
 * Drei Dinge, die das UI aussprechen muss und nie einebnen darf:
 *
 * * **Der Anteil ist ein Bruch, keine Prozentzahl.** Drei Erben zu je 1/3 sind
 *   dezimal nicht darstellbar; „33,33 % dreimal" ergäbe 99,99 %, und ein
 *   vollständiger Eigentumsstand wäre nie erreichbar. Deshalb Zähler und
 *   Nenner — und `anteil_text` vom Server, damit die Umrechnung an einer
 *   Stelle liegt.
 * * **„Nicht erfasst" ist nicht „gehört niemandem".** Eine Einheit ohne Stand
 *   (`eigentum === null`) heißt: Niemand hat es eingetragen.
 * * **Der Vollständigkeitsgrad ist der Kern, nicht ein Nebenfeld.** Der Alltag
 *   beginnt bei „teilweise geklärt" — man kennt einen von vier Eigentümern.
 *   Das Modell zwingt niemanden, etwas zu behaupten, was er nicht weiß.
 */

/** Wie vollständig ist die Aussage über das Eigentum? */
export type Vollstaendigkeit = 'COMPLETE' | 'PARTIAL' | 'UNRESOLVED';

/** Woher stammt die Angabe? Pflichtfeld (Beschluss A-14). */
export type Quellenart =
  | 'MANAGEMENT_NOTICE'
  | 'OWNER_LIST'
  | 'ORDER_STATEMENT'
  | 'IMPORT'
  | 'MANUAL';

export type Eigentumsart = 'SOLE' | 'CO_OWNER';
export type Bestaetigung = 'CONFIRMED' | 'UNCONFIRMED';

export interface Eigentuemer {
  id: string;
  party_id: string;
  display_name: string;
  /** Zähler des Bruchs; null = Anteil unbekannt (in COMPLETE unzulässig). */
  share_numerator: number | null;
  share_denominator: number | null;
  /** Lesbarer Anteil vom Server: „50 %", „1/3" oder „unbekannt". */
  anteil_text: string;
  ownership_type: Eigentumsart;
  confirmation_status: Bestaetigung;
}

export interface Eigentumsstand {
  id: string;
  unit_id: string;
  unit_number: string;
  unit_type: string;
  distribution_status: Vollstaendigkeit;
  valid_from: string;
  valid_until: string | null;
  /** Gilt der Stand heute? Der Server rechnet es, das UI zeigt es nur. */
  is_current: boolean;
  source_type: Quellenart;
  source_reference: string;
  confirmed_at: string | null;
  eigentuemer: Eigentuemer[];
}

export interface EinheitEigentum {
  unit_id: string;
  unit_number: string;
  unit_type: string;
  /** A-08: Gemeinschafts- und Technikflächen tragen keinen Eigentumsstand. */
  eigentumsfaehig: boolean;
  /** null = nicht erfasst. NICHT „gehört niemandem". */
  eigentum: Eigentumsstand | null;
}

/** Ein Eigentümer der Liegenschaft — für die Auswahl als Rechnungsempfänger. */
export interface EigentuemerRef {
  party_id: string;
  display_name: string;
}

// --- Schreib-Verträge ------------------------------------------------------

export interface EigentuemerIn {
  party_id: string;
  share_numerator?: number | null;
  share_denominator?: number | null;
  ownership_type?: Eigentumsart;
  confirmation_status?: Bestaetigung;
}

export interface EigentumIn {
  unit_id: string;
  valid_from: string;
  source_type: Quellenart;
  source_reference: string;
  distribution_status?: Vollstaendigkeit;
  valid_until?: string | null;
  eigentuemer?: EigentuemerIn[];
}

export interface EigentumPatch {
  distribution_status?: Vollstaendigkeit;
  valid_from?: string;
  valid_until?: string | null;
  source_type?: Quellenart;
  source_reference?: string;
}

export interface EigentuemerPatch {
  share_numerator?: number | null;
  share_denominator?: number | null;
  ownership_type?: Eigentumsart;
  confirmation_status?: Bestaetigung;
}

// --- Beschriftungen --------------------------------------------------------

/**
 * Die Vollständigkeitsgrade in der Sprache des Betriebs.
 *
 * „UNRESOLVED" heißt nicht „Fehler", sondern „wir wissen es noch nicht" — und
 * das muss das Wort transportieren, sonst fühlt sich der Normalfall wie ein
 * Mangel an.
 */
export const VOLLSTAENDIGKEIT_LABEL: Record<Vollstaendigkeit, string> = {
  COMPLETE: 'Vollständig geklärt',
  PARTIAL: 'Teilweise geklärt',
  UNRESOLVED: 'Ungeklärt',
};

export const VOLLSTAENDIGKEIT_HINWEIS: Record<Vollstaendigkeit, string> = {
  COMPLETE:
    'Das sind alle Eigentümer, und die Anteile ergeben zusammen genau 1. Jeder Anteil muss beziffert und bestätigt sein.',
  PARTIAL:
    'Einzelne Eigentümer sind bekannt, die Aufteilung ist es noch nicht. Anteile dürfen fehlen.',
  UNRESOLVED: 'Zum Eigentum liegt noch nichts vor.',
};

export const VOLLSTAENDIGKEIT_OPTIONEN: { wert: Vollstaendigkeit; label: string }[] = [
  { wert: 'UNRESOLVED', label: VOLLSTAENDIGKEIT_LABEL.UNRESOLVED },
  { wert: 'PARTIAL', label: VOLLSTAENDIGKEIT_LABEL.PARTIAL },
  { wert: 'COMPLETE', label: VOLLSTAENDIGKEIT_LABEL.COMPLETE },
];

export const QUELLENART_LABEL: Record<Quellenart, string> = {
  MANAGEMENT_NOTICE: 'Mitteilung der Verwaltung',
  OWNER_LIST: 'Eigentümerliste',
  ORDER_STATEMENT: 'Angabe im Auftrag',
  IMPORT: 'Datenübernahme',
  MANUAL: 'Manuell erfasst',
};

export const QUELLENART_OPTIONEN: { wert: Quellenart; label: string }[] = (
  Object.keys(QUELLENART_LABEL) as Quellenart[]
).map((wert) => ({ wert, label: QUELLENART_LABEL[wert] }));

export const EIGENTUMSART_LABEL: Record<Eigentumsart, string> = {
  SOLE: 'Alleineigentum',
  CO_OWNER: 'Miteigentum',
};

export const EIGENTUMSART_OPTIONEN: { wert: Eigentumsart; label: string }[] = [
  { wert: 'CO_OWNER', label: EIGENTUMSART_LABEL.CO_OWNER },
  { wert: 'SOLE', label: EIGENTUMSART_LABEL.SOLE },
];

/**
 * Statusklasse des Vollständigkeitsgrads.
 *
 * WCAG 1.4.1: Der Grad steht IMMER als Text da; die Farbe ist Zugabe. „Teilweise
 * geklärt" bekommt bewusst keine Warnfarbe — es ist der Normalfall, kein Mangel.
 */
export function vollstaendigkeitClass(wert: Vollstaendigkeit): string {
  if (wert === 'COMPLETE') return 'stamp stamp--positive';
  return 'stamp';
}
