// Vertrag zu /api/oeffentlich/angebot/{token} — die einzige anmeldefreie Sicht.
//
// Bewusst ein EIGENES Modell und keine Wiederverwendung von `QuoteDetail`:
// Der Server baut die Antwort als Positivliste (kein `unit_cost`, kein
// `markup_percent`, kein `tax_code`); ein geteiltes Interface würde Felder
// versprechen, die es hier nie gibt — und beim nächsten neuen Betragsfeld
// stillschweigend so tun, als käme es mit.
//
// Beträge sind **Strings** (Decimal), wie überall in dieser App. Nie
// `parseFloat` ins Datenmodell, nur zur Anzeige.

export interface OeffentlichePosition {
  position_number: number;
  line_type: string;
  /** NORMAL | ALTERNATIV | BEDARF — Alternativ/Bedarf zählen nicht in die Summe. */
  line_kind: string;
  /** Abschnittsnummer (1-basiert) oder null. */
  rubrik: number | null;
  description: string;
  quantity: string | null;
  unit: string | null;
  unit_price: string | null;
  discount_percent: string | null;
  tax_rate_percent: string | null;
  net_amount: string | null;
}

export interface OeffentlicheRubrik {
  position_number: number;
  title: string;
  description: string | null;
}

export interface OeffentlicherAussteller {
  company_name: string;
  street: string | null;
  postal_code: string | null;
  city: string | null;
  phone: string | null;
  email: string | null;
  web: string | null;
}

export interface OeffentlichesAngebot {
  quote_number: string | null;
  title: string;
  status: string;
  quote_date: string | null;
  valid_until_date: string | null;
  currency: string;
  net_total: string | null;
  tax_total: string | null;
  gross_total: string | null;
  cover_letter: string | null;
  objekt: string | null;
  aussteller: OeffentlicherAussteller | null;
  rubriken: OeffentlicheRubrik[];
  positionen: OeffentlichePosition[];
  /** Darf jetzt entschieden werden? False, wenn der Ausgang schon feststeht. */
  entscheidbar: boolean;
  ausgang: string | null;
  /** Wann der Ausgang festgehalten wurde (ISO). Null, solange offen. */
  ausgang_am: string | null;
  link_gueltig_bis: string;
}

export type Entscheidung = 'ANGENOMMEN' | 'ABGELEHNT';

export interface EntscheidungErgebnis {
  ausgang: string;
  meldung: string;
  quote_number: string | null;
  title: string;
}

/** Beschriftung eines Ausgangs — Status nie allein über Farbe (WCAG 2.2 AA). */
export function ausgangLabel(status: string): string {
  switch (status) {
    case 'ANGENOMMEN':
      return 'Angenommen';
    case 'ABGELEHNT':
      return 'Abgelehnt';
    case 'ABGELAUFEN':
      return 'Abgelaufen';
    case 'ERSETZT':
      return 'Ersetzt';
    case 'VERSENDET':
      return 'Offen';
    default:
      return status;
  }
}
