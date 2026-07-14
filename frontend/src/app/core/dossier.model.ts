/**
 * Entitäts-Dossier — alles zu EINER Entität in EINEM Aufruf.
 *
 * Spiegelt `backend/api/dossier.py` (Schemata) 1:1. Vier Dossiers: Kontakt,
 * Liegenschaft, Projekt, Auftrag.
 *
 * ## Zwei Doktrinen, die dieses Modell trägt (nicht „vereinfachen")
 *
 * **1. Ein nicht sichtbarer Baustein ist NICHT leer.** Fehlt dem Aufrufer das
 * Modulrecht, liefert der Server den Baustein als `null` und dazu ein Flag
 * `<baustein>_sichtbar: false`. Das ist etwas grundsätzlich anderes als „es gibt
 * nichts" (dann: leere Liste, Flag `true`). Beides sähe im UI gleich aus, wenn
 * man es gleich behandelt — deshalb sind die Flags hier Pflichtfelder und die
 * Nutzlast ist `| null`. Die Ansicht muss den Unterschied AUSSPRECHEN.
 *
 * **2. Geld und Mengen sind Strings (Decimal).** Sie werden nie in `number`
 * überführt — außer unmittelbar zur Anzeige. `null` heißt **unbekannt**, niemals
 * 0,00 € / 0 % / 0 h. Der Server rechnet verbindlich; das Frontend rechnet nicht.
 */
import { OffeneAbrechnung } from './auftrag.model';
import { Marge } from './auswertungen.model';
import { SollIst } from './site-report.model';

export type DossierTyp = 'kontakt' | 'liegenschaft' | 'projekt' | 'auftrag';

/** Ein Modulrecht, das für einen Baustein fehlen kann (für den Klartext-Hinweis). */
export type DossierModul =
  | 'identity'
  | 'property'
  | 'workflow'
  | 'invoicing'
  | 'pricing'
  | 'content'
  | 'maintenance';

// ---------------------------------------------------------------------------
// Gemeinsame Bausteine
// ---------------------------------------------------------------------------

export interface OffenerPosten {
  invoice_id: string;
  invoice_number: string | null;
  invoice_type: string;
  invoice_date: string | null;
  due_date: string | null;
  gross_total: string;
  paid_total: string;
  /** Storno/Gutschrift zu dieser Rechnung (≤ 0) — mindert die Forderung. */
  credit_total: string;
  open_amount: string;
  payment_status: string;
  is_overdue: boolean;
  days_overdue: number | null;
  dunning_level: number | null;
}

export interface OffenePosten {
  posten: OffenerPosten[];
  anzahl: number;
  summe_offen: string;
  anzahl_ueberfaellig: number;
  summe_ueberfaellig: string;
}

/**
 * Zahlungsverhalten. `durchschnittliche_verzoegerung_tage` ist `null`, solange
 * keine Rechnung bezahlt wurde — **nicht 0**. Eine 0 hieße „zahlt pünktlich" und
 * wäre eine Behauptung über einen Kunden, über den wir nichts wissen.
 */
export interface Zahlungsverhalten {
  rechnungen_gesamt: number;
  bezahlt_anzahl: number;
  offen_anzahl: number;
  ueberfaellig_anzahl: number;
  summe_offen: string;
  summe_ueberfaellig: string;
  durchschnittliche_verzoegerung_tage: number | null;
  groesste_verzoegerung_tage: number | null;
  bewertete_rechnungen: number;
}

export interface DossierDokument {
  file_id: string;
  link_id: string;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  link_category: string | null;
  uploaded_at: string;
  uploaded_by: string | null;
}

export interface DossierVorgang {
  id: string;
  case_number: string;
  subject: string;
  status: string;
  priority: string;
  received_at: string;
  is_offen: boolean;
  property_id: string | null;
  project_id: string | null;
}

export interface DossierAuftrag {
  id: string;
  order_number: string;
  title: string;
  status: string;
  priority: string;
  billing_mode: string;
  is_offen: boolean;
  desired_date: string | null;
  property_id: string | null;
  project_id: string | null;
}

export interface DossierEinsatz {
  id: string;
  job_number: string;
  title: string | null;
  status: string;
  scheduled_start: string | null;
  scheduled_end: string | null;
  work_order_id: string | null;
  zugewiesen: string[];
}

export interface DossierAufgabe {
  id: string;
  title: string;
  status: string;
  due_date: string | null;
  assigned_to: string | null;
}

export interface DossierBeleg {
  id: string;
  invoice_number: string | null;
  invoice_type: string;
  status: string;
  invoice_date: string | null;
  net_total: string | null;
  gross_total: string | null;
  work_order_id: string | null;
}

export interface DossierAngebot {
  id: string;
  quote_number: string | null;
  title: string;
  status: string;
  quote_date: string | null;
  net_total: string | null;
  gross_total: string | null;
  work_order_id: string | null;
}

/**
 * Angebotszeile der **Objektsicht** (row_scope EIGENE, Migration 0102) — ohne Betrag.
 *
 * Erbt bewusst **nicht** von `DossierAngebot`: Trüge sie `net_total`/`gross_total` im
 * Typ, zeigte sie irgendein Template irgendwann an. Der Server schickt die Felder
 * nicht; der Typ kennt sie nicht.
 */
export interface DossierAngebotMengen {
  id: string;
  quote_number: string | null;
  title: string;
  status: string;
  quote_date: string | null;
  work_order_id: string | null;
}

// ---------------------------------------------------------------------------
// Kontakt
// ---------------------------------------------------------------------------

export interface KontaktKern {
  id: string;
  party_type: string;
  display_name: string;
  status: string;
  first_name: string | null;
  last_name: string | null;
  salutation: string | null;
  legal_name: string | null;
  organization_type: string | null;
  vat_id: string | null;
  acquisition_source: string | null;
}

export interface DossierAdresse {
  address_type: string;
  is_primary: boolean;
  street: string;
  house_number: string | null;
  postal_code: string;
  city: string;
  country_code: string;
}

export interface DossierKontaktweg {
  contact_type: string;
  value: string;
  label: string | null;
  is_primary: boolean;
}

export interface DossierAnsprechpartner {
  person_party_id: string;
  display_name: string;
  valid_from: string;
}

export interface PartyLiegenschaft {
  property_id: string;
  property_number: string;
  name: string;
  city: string;
  role: string;
  valid_from: string;
  valid_until: string | null;
  is_current: boolean;
}

export interface DossierKommunikation {
  id: string;
  channel: string;
  direction: string;
  subject: string | null;
  occurred_at: string;
  counterpart: string | null;
}

export interface KontaktDossier {
  kontakt: KontaktKern;
  adressen: DossierAdresse[];
  kontaktwege: DossierKontaktweg[];
  ansprechpartner: DossierAnsprechpartner[];
  liegenschaften_sichtbar: boolean;
  liegenschaften: PartyLiegenschaft[] | null;
  vorgaenge_sichtbar: boolean;
  vorgaenge: DossierVorgang[] | null;
  auftraege: DossierAuftrag[] | null;
  aufgaben_sichtbar: boolean;
  aufgaben: DossierAufgabe[] | null;
  offene_posten_sichtbar: boolean;
  offene_posten: OffenePosten | null;
  zahlungsverhalten_sichtbar: boolean;
  zahlungsverhalten: Zahlungsverhalten | null;
  kommunikation_sichtbar: boolean;
  kommunikation: DossierKommunikation[] | null;
  dokumente_sichtbar: boolean;
  dokumente: DossierDokument[] | null;
}

// ---------------------------------------------------------------------------
// Liegenschaft
// ---------------------------------------------------------------------------

export interface LiegenschaftKern {
  id: string;
  property_number: string;
  name: string;
  property_type: string;
  status: string;
  street: string;
  house_number: string | null;
  postal_code: string;
  city: string;
}

export interface DossierEinheit {
  unit_id: string;
  unit_type: string;
  unit_number: string;
}

export interface DossierGebaeude {
  building_id: string;
  building_number: string;
  name: string | null;
  units: DossierEinheit[];
}

export interface DossierAnlage {
  id: string;
  name: string;
  asset_type: string | null;
  building_id: string | null;
  unit_id: string | null;
}

export interface DossierBeteiligter {
  party_id: string;
  display_name: string;
  role: string;
  valid_from: string;
  valid_until: string | null;
  is_current: boolean;
}

/**
 * Zutrittshinweis **mit Herkunft**. Es gibt kein Zutrittsfeld an der
 * Liegenschaft — jeder Hinweis stammt von einem Einsatz und wird mit diesem
 * ausgewiesen. Der Nutzer sieht, woher die Angabe kommt und wie alt sie ist.
 */
export interface Zutrittshinweis {
  service_job_id: string;
  job_number: string;
  scheduled_start: string | null;
  work_order_id: string | null;
  work_order_number: string | null;
  hinweis: string;
}

export interface DossierFaelligkeit {
  id: string;
  kind: string; // WARTUNG | PRUEFUNG | GEWAEHRLEISTUNG
  title: string;
  due_date: string;
  status: string;
  is_ueberfaellig: boolean;
}

export interface DossierWartungsvertrag {
  id: string;
  contract_number: string;
  name: string;
  status: string;
  interval_kind: string;
  next_due_date: string | null;
  due_action: string;
}

export interface LiegenschaftDossier {
  liegenschaft: LiegenschaftKern;
  gebaeude: DossierGebaeude[];
  anlagen: DossierAnlage[];
  beteiligte: DossierBeteiligter[];
  vorgaenge_sichtbar: boolean;
  vorgaenge: DossierVorgang[] | null;
  auftraege: DossierAuftrag[] | null;
  einsaetze: DossierEinsatz[] | null;
  zutrittshinweise: Zutrittshinweis[] | null;
  wartung_sichtbar: boolean;
  faelligkeiten: DossierFaelligkeit[] | null;
  wartungsvertraege: DossierWartungsvertrag[] | null;
  offene_posten_sichtbar: boolean;
  offene_posten: OffenePosten | null;
  dokumente_sichtbar: boolean;
  dokumente: DossierDokument[] | null;
}

// ---------------------------------------------------------------------------
// Projekt
// ---------------------------------------------------------------------------

export interface ProjektKern {
  id: string;
  project_number: string;
  name: string;
  status: string;
  start_date: string | null;
  target_end_date: string | null;
  category: string | null;
}

export interface ProjektLiegenschaft {
  property_id: string;
  property_number: string;
  name: string;
  city: string;
}

export interface DossierChecklistItem {
  id: string;
  position: number;
  label: string;
  is_done: boolean;
  done_at: string | null;
}

export interface DossierChecklist {
  id: string;
  name: string;
  items: DossierChecklistItem[];
}

export interface DossierLogbuch {
  id: string;
  category: string;
  entry: string;
  created_at: string;
  author: string | null;
}

/** Anrechenbarer Abschlag — vom Server geliefert, nie im Frontend gerechnet. */
export interface DossierAbschlag {
  work_order_id: string;
  invoice_id: string;
  invoice_number: string | null;
  invoice_type: string;
  invoice_date: string | null;
  net_total: string | null;
  gross_total: string | null;
  vorgemerkt: boolean;
}

export interface ProjektDossier {
  projekt: ProjektKern;
  liegenschaften: ProjektLiegenschaft[];
  vorgaenge: DossierVorgang[];
  auftraege: DossierAuftrag[];
  checklisten: DossierChecklist[];
  logbuch: DossierLogbuch[];
  aufgaben: DossierAufgabe[];
  belege_sichtbar: boolean;
  angebote: DossierAngebot[] | null;
  rechnungen: DossierBeleg[] | null;
  anrechenbare_abschlaege: DossierAbschlag[] | null;
  /**
   * Objektsicht (row_scope EIGENE): dieselben Angebote **ohne Beträge**, und nur die
   * versendeten/angenommenen an MEINEN Liegenschaften dieses Projekts. Schließt
   * `belege_sichtbar` aus — beide Flags sind nie gleichzeitig true.
   */
  angebote_mengen_sichtbar: boolean;
  angebote_mengen: DossierAngebotMengen[] | null;
  offene_posten_sichtbar: boolean;
  offene_posten: OffenePosten | null;
  /** Marge braucht `invoicing` UND `pricing` — Umsatz minus Einkauf. */
  marge_sichtbar: boolean;
  marge: Marge | null;
  geplante_marge: Marge | null;
  dokumente_sichtbar: boolean;
  dokumente: DossierDokument[] | null;
}

// ---------------------------------------------------------------------------
// Auftrag
// ---------------------------------------------------------------------------

export interface AuftragKern {
  id: string;
  order_number: string;
  title: string;
  description: string | null;
  status: string;
  priority: string;
  billing_mode: string;
  is_emergency: boolean;
  desired_date: string | null;
  responsibility_scope: string;
  responsibility_confirmed_at: string | null;
  order_evidence_reference: string | null;
  property_id: string;
  property_name: string;
  property_city: string;
  project_id: string | null;
  project_name: string | null;
  service_case_number: string | null;
}

/**
 * Ein möglicher Statusübergang. **Möglich heißt nicht erlaubt und nicht
 * zulässig** — die Rechtematrix und die DB-Tore entscheiden darüber. Diese Liste
 * sagt nur, welche Ziele der Statusautomat kennt.
 */
export interface DossierUebergang {
  to_status: string;
  begruendung_pflicht: boolean;
}

export interface AuftragBeteiligter {
  party_id: string;
  display_name: string;
  role: string;
  is_primary: boolean;
}

export interface DossierZeiteintrag {
  id: string;
  started_at: string;
  ended_at: string | null;
  /** Laufende Buchung → Dauer **unbekannt** (`null`), nicht 0. */
  stunden: string | null;
  kategorie: string;
  is_work_time: boolean;
  mitarbeiter: string;
  note: string | null;
}

export interface DossierZeiten {
  eintraege: DossierZeiteintrag[];
  laufende: number;
  /** Keine abgeschlossene Arbeitszeitbuchung → `null` (unbekannt), nie 0,0 h. */
  summe_arbeitsstunden: string | null;
}

export interface DossierMaterial {
  id: string;
  description: string;
  quantity: string;
  unit: string;
  note: string | null;
  service_job_id: string;
}

export interface DossierBericht {
  id: string;
  report_date: string;
  status: string;
  activity_text: string;
  hours_worked: string | null;
  author: string | null;
  signed_at: string | null;
  signed_by_name: string | null;
}

export interface AuftragDossier {
  auftrag: AuftragKern;
  moegliche_uebergaenge: DossierUebergang[];
  beteiligte: AuftragBeteiligter[];
  einsaetze: DossierEinsatz[];
  zeiten: DossierZeiten;
  material: DossierMaterial[];
  berichte: DossierBericht[];
  soll_ist: SollIst;
  abrechnung_sichtbar: boolean;
  abrechnung: OffeneAbrechnung | null;
  belege_sichtbar: boolean;
  angebote: DossierAngebot[] | null;
  rechnungen: DossierBeleg[] | null;
  /** Objektsicht: „Was ist beauftragt?" — ohne Betrag (Migration 0102). */
  angebote_mengen_sichtbar: boolean;
  angebote_mengen: DossierAngebotMengen[] | null;
  offene_posten_sichtbar: boolean;
  offene_posten: OffenePosten | null;
  dokumente_sichtbar: boolean;
  dokumente: DossierDokument[] | null;
}

// ---------------------------------------------------------------------------
// Anzeige-Helfer (Labels — Status nie nur über Farbe, WCAG 2.2 AA / 1.4.1)
// ---------------------------------------------------------------------------

const VORGANG_STATUS: Record<string, string> = {
  NEU: 'Neu',
  IN_PRUEFUNG: 'In Prüfung',
  RUECKFRAGE: 'Rückfrage',
  FREIGABE_AUSSTEHEND: 'Freigabe ausstehend',
  BEAUFTRAGT: 'Beauftragt',
  ABGESCHLOSSEN: 'Abgeschlossen',
  ABGELEHNT: 'Abgelehnt',
};

const AUFGABE_STATUS: Record<string, string> = {
  OFFEN: 'Offen',
  ERLEDIGT: 'Erledigt',
  VERWORFEN: 'Verworfen',
};

const PROJEKT_STATUS: Record<string, string> = {
  OPEN: 'Offen',
  CLOSED: 'Geschlossen',
};

const LIEGENSCHAFT_TYP: Record<string, string> = {
  EINFAMILIENHAUS: 'Einfamilienhaus',
  WEG: 'WEG',
  RENTAL_PROPERTY: 'Mietobjekt',
  COMMERCIAL: 'Gewerbe',
  MIXED: 'Gemischt',
  OTHER: 'Sonstige',
};

const EINHEIT_TYP: Record<string, string> = {
  APARTMENT: 'Wohnung',
  COMMERCIAL: 'Gewerbe',
  GARAGE: 'Garage',
  PARKING: 'Stellplatz',
  STORAGE: 'Lager',
  COMMON_AREA: 'Gemeinschaft',
  TECHNICAL_ROOM: 'Technikraum',
  OTHER: 'Sonstige',
};

/** identity.party.status bzw. property.status — beide nutzen ACTIVE/INACTIVE. */
const STAMM_STATUS: Record<string, string> = {
  ACTIVE: 'Aktiv',
  INACTIVE: 'Inaktiv',
  MERGED: 'Zusammengeführt',
};

const BELEG_STATUS: Record<string, string> = {
  ENTWURF: 'Entwurf',
  INTERN_GEPRUEFT: 'Intern geprüft',
  FREIGEGEBEN: 'Freigegeben',
  VERSENDET: 'Versendet',
  ANGENOMMEN: 'Angenommen',
  ABGELEHNT: 'Abgelehnt',
  ABGELAUFEN: 'Abgelaufen',
  ERSETZT: 'Ersetzt',
  VEROEFFENTLICHT: 'Veröffentlicht',
  STORNIERT: 'Storniert',
  BEZAHLT: 'Bezahlt',
};

const PRIORITAET: Record<string, string> = {
  NORMAL: 'Normal',
  DRINGEND: 'Dringend',
  NOTFALL: 'Notfall',
};

/**
 * Rollen aus DREI Quellen unter einem Dach: Liegenschaftsrollen
 * (`property.property_party`), Auftragsrollen (`workflow.work_order_party`) und
 * die Rolle eines Kontakts an einer Liegenschaft. Die Codes überschneiden sich
 * nicht — deshalb genügt eine Tabelle. Unbekanntes bleibt der Rohcode.
 */
const ROLLE: Record<string, string> = {
  // Liegenschaft
  COMMUNITY_OF_OWNERS: 'Eigentümergemeinschaft',
  PROPERTY_OWNER: 'Eigentümer',
  OPERATOR: 'Betreiber',
  CARETAKER: 'Hausmeisterei',
  // Auftrag
  PRINCIPAL: 'Auftraggeber',
  REPRESENTATIVE: 'Vertretung',
  SERVICE_RECIPIENT: 'Leistungsempfänger',
  OCCUPANT: 'Nutzer',
  COST_BEARER: 'Kostenträger',
  INVOICE_DEBTOR: 'Rechnungsschuldner',
  INVOICE_RECIPIENT: 'Rechnungsempfänger',
  REPORTER: 'Melder',
  ON_SITE_CONTACT: 'Ansprechpartner vor Ort',
};

const ADRESSART: Record<string, string> = {
  BUSINESS: 'Geschäftsanschrift',
  POSTAL: 'Postanschrift',
  BILLING: 'Rechnungsanschrift',
  PRIVATE: 'Privatanschrift',
};

const KONTAKTART: Record<string, string> = {
  EMAIL: 'E-Mail',
  PHONE: 'Telefon',
  MOBILE: 'Mobil',
  FAX: 'Fax',
  PORTAL: 'Portal',
};

const KANAL: Record<string, string> = {
  EMAIL: 'E-Mail',
  TELEFONNOTIZ: 'Telefonnotiz',
  SMS_MESSENGER: 'SMS/Messenger',
  PORTAL: 'Portal',
  BRIEF: 'Brief',
  GESPRAECHSNOTIZ: 'Gesprächsnotiz',
};

/** `work_order.responsibility_scope` — WEG-Zuständigkeit. */
const VERANTWORTUNG: Record<string, string> = {
  UNKNOWN: 'Ungeklärt',
  COMMON_PROPERTY: 'Gemeinschaftseigentum',
  PRIVATE_UNIT: 'Sondereigentum',
  MIXED: 'Gemischt',
};

const RICHTUNG: Record<string, string> = {
  EINGEHEND: 'Eingehend',
  AUSGEHEND: 'Ausgehend',
  INTERN: 'Intern',
};

function aus(map: Record<string, string>, code: string | null): string {
  if (!code) return '—';
  return map[code] ?? code;
}

export const dossierVorgangStatus = (s: string) => aus(VORGANG_STATUS, s);
export const dossierAufgabeStatus = (s: string) => aus(AUFGABE_STATUS, s);
export const dossierProjektStatus = (s: string) => aus(PROJEKT_STATUS, s);
export const dossierBelegStatus = (s: string) => aus(BELEG_STATUS, s);
export const dossierPrioritaet = (s: string) => aus(PRIORITAET, s);
export const dossierRolle = (s: string) => aus(ROLLE, s);
export const dossierAdressart = (s: string) => aus(ADRESSART, s);
export const dossierKontaktart = (s: string) => aus(KONTAKTART, s);
export const dossierKanal = (s: string) => aus(KANAL, s);
export const dossierRichtung = (s: string) => aus(RICHTUNG, s);
export const dossierVerantwortung = (s: string) => aus(VERANTWORTUNG, s);
export const dossierLiegenschaftTyp = (s: string) => aus(LIEGENSCHAFT_TYP, s);
export const dossierEinheitTyp = (s: string) => aus(EINHEIT_TYP, s);
export const dossierStammStatus = (s: string) => aus(STAMM_STATUS, s);

/** ACTIVE ist positiv — der Text steht immer daneben (WCAG 1.4.1). */
export const dossierStammStatusClass = (s: string) => (s === 'ACTIVE' ? 'stamp--positive' : '');

/** „1 Rechnung" / „2 Rechnungen" — kein „(1 Rechnungen)". */
export function anzahlRechnungen(n: number): string {
  return `${n} ${n === 1 ? 'Rechnung' : 'Rechnungen'}`;
}

/** Kurzname des Moduls für den ehrlichen „Recht fehlt"-Hinweis. */
export const MODUL_KLARTEXT: Record<DossierModul, string> = {
  identity: 'Kontakte',
  property: 'Liegenschaften',
  workflow: 'Vorgänge & Aufträge',
  invoicing: 'Belege & Buchhaltung',
  pricing: 'Preise & Artikel',
  content: 'Dokumente & Kommunikation',
  maintenance: 'Wartung',
};
