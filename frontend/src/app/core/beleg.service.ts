import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import {
  AnrechenbarerAbschlag,
  InvoiceCreate,
  InvoiceUpdate,
  InvoiceDetail,
  InvoiceEmailResult,
  InvoicePage,
  InvoicePartyCreate,
  InvoiceQuery,
  Kalkulation,
  QuoteAusgang,
  QuoteCopy,
  QuoteCreate,
  QuoteDetail,
  QuoteEmailResult,
  QuoteLineInput,
  QuoteMengenDetail,
  QuoteMengenPage,
  QuotePage,
  QuoteQuery,
  QuoteUpdate,
  RechnungAusAngebot,
  RechnungAusAuftrag,
  RechnungAusNachtrag,
} from './beleg.model';

/** Typisierter Zugriff auf die Beleg-API (dev-Proxy: /api -> :8000). */
@Injectable({ providedIn: 'root' })
export class BelegService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/invoicing/quotes';

  list(query: QuoteQuery): Observable<QuotePage> {
    let params = new HttpParams()
      .set('page', query.page)
      .set('page_size', query.page_size);
    const q = query.q?.trim();
    if (q) params = params.set('q', q);
    if (query.status) params = params.set('status', query.status);
    if (query.property_id) params = params.set('property_id', query.property_id);
    if (query.project_id) params = params.set('project_id', query.project_id);
    return this.http.get<QuotePage>(this.base, { params });
  }

  get(id: string): Observable<QuoteDetail> {
    return this.http.get<QuoteDetail>(`${this.base}/${id}`);
  }

  /**
   * Angebote **ohne Preise** (Migration 0102) — Mengen und Einheiten.
   *
   * Für row_scope EIGENE (Monteur) der **einzige** Angebotspfad: `list`/`get` oben
   * antworten ihm mit 403. Er sieht hier nur die versendeten/angenommenen Angebote
   * an seinen Objekten; alles andere ist 404.
   */
  listQuotesMengen(query: QuoteQuery): Observable<QuoteMengenPage> {
    let params = new HttpParams()
      .set('page', query.page)
      .set('page_size', query.page_size);
    const q = query.q?.trim();
    if (q) params = params.set('q', q);
    if (query.status) params = params.set('status', query.status);
    if (query.property_id) params = params.set('property_id', query.property_id);
    if (query.project_id) params = params.set('project_id', query.project_id);
    return this.http.get<QuoteMengenPage>(`${this.base}/mengen`, { params });
  }

  getQuoteMengen(id: string): Observable<QuoteMengenDetail> {
    return this.http.get<QuoteMengenDetail>(`${this.base}/${id}/mengen`);
  }

  /**
   * Interne Kalkulationsübersicht je Abschnitt (EK, Deckungsbeitrag, Marge).
   * Verlangt `pricing/LESEN` — wer den Beleg lesen darf, sieht nicht zwingend die
   * Marge. Ein 403 ist hier also ein normaler Zustand, kein Fehler.
   */
  kalkulation(id: string): Observable<Kalkulation> {
    return this.http.get<Kalkulation>(`${this.base}/${id}/kalkulation`);
  }

  invoiceKalkulation(id: string): Observable<Kalkulation> {
    return this.http.get<Kalkulation>(`/api/invoicing/invoices/${id}/kalkulation`);
  }

  listInvoices(query: InvoiceQuery): Observable<InvoicePage> {
    let params = new HttpParams()
      .set('page', query.page)
      .set('page_size', query.page_size);
    const q = query.q?.trim();
    if (q) params = params.set('q', q);
    if (query.status) params = params.set('status', query.status);
    if (query.invoice_type) params = params.set('invoice_type', query.invoice_type);
    if (query.property_id) params = params.set('property_id', query.property_id);
    if (query.project_id) params = params.set('project_id', query.project_id);
    return this.http.get<InvoicePage>('/api/invoicing/invoices', { params });
  }

  getInvoice(id: string): Observable<InvoiceDetail> {
    return this.http.get<InvoiceDetail>(`/api/invoicing/invoices/${id}`);
  }

  // --- Schreibend (Session-Auth Pflicht) -----------------------------------

  /** Neues Angebot (Status ENTWURF) mit Positionen anlegen. */
  createQuote(payload: QuoteCreate): Observable<QuoteDetail> {
    return this.http.post<QuoteDetail>(this.base, payload);
  }

  /**
   * Angebotsentwurf speichern (PUT). Positionen und Abschnitte werden vollständig
   * ersetzt — der Editor schickt immer den ganzen Beleg. 422, wenn das Angebot
   * bereits versendet ist. Der Server berechnet Summen und Kalkulation neu.
   */
  updateQuote(id: string, payload: QuoteUpdate): Observable<QuoteDetail> {
    return this.http.put<QuoteDetail>(`${this.base}/${id}`, payload);
  }

  /** Angebot versenden — unumkehrbar: DB vergibt die AN-Nummer und friert ein. */
  sendQuote(id: string): Observable<QuoteDetail> {
    return this.http.post<QuoteDetail>(`${this.base}/${id}/send`, {});
  }

  /**
   * Angebot als **neuen Entwurf** duplizieren (Kopf „… (Kopie)", Abschnitte,
   * Positionen wertgleich). Ziel-Liegenschaft/-Projekt optional (Default: wie
   * Quelle). Aus jedem Status kopierbar; das Ergebnis ist ein frischer ENTWURF
   * ohne Snapshot (GoBD). Der Auftragsbezug wird nicht mitkopiert.
   */
  copyQuote(id: string, ziel: QuoteCopy = {}): Observable<QuoteDetail> {
    return this.http.post<QuoteDetail>(`${this.base}/${id}/kopie`, ziel);
  }

  /**
   * Versendetes Angebot als PDF-Anhang per E-Mail versenden. `toAddress`
   * überschreibt die serverseitig best-effort abgeleitete Empfänger-Adresse.
   * Reine Zustellung (kein Statuswechsel); Fehler (kein Empfänger/Konto, SMTP)
   * kommen als 422.
   */
  sendQuoteEmail(id: string, toAddress: string): Observable<QuoteEmailResult> {
    return this.http.post<QuoteEmailResult>(
      `${this.base}/${id}/send-email`,
      { to_address: toAddress },
    );
  }

  /** Neue Rechnung/Gutschrift (Status ENTWURF) mit Positionen anlegen. */
  createInvoice(payload: InvoiceCreate): Observable<InvoiceDetail> {
    return this.http.post<InvoiceDetail>('/api/invoicing/invoices', payload);
  }

  /** Rechnungsentwurf ändern (Positionen/Abschnitte werden vollständig ersetzt). */
  updateInvoice(id: string, payload: InvoiceUpdate): Observable<InvoiceDetail> {
    return this.http.put<InvoiceDetail>(`/api/invoicing/invoices/${id}`, payload);
  }

  /**
   * Die anrechenbaren Abschlags-/Teilrechnungen eines Auftrags (für die
   * Schlussrechnung): veröffentlicht, nicht storniert und von keiner
   * veröffentlichten Schlussrechnung angerechnet. `finalInvoiceId` markiert
   * zusätzlich, was der genannte Entwurf schon anrechnet (`angerechnet`).
   */
  anrechenbareAbschlaege(
    workOrderId: string,
    finalInvoiceId?: string | null,
  ): Observable<AnrechenbarerAbschlag[]> {
    let params = new HttpParams().set('work_order_id', workOrderId);
    if (finalInvoiceId) params = params.set('final_invoice_id', finalInvoiceId);
    return this.http.get<AnrechenbarerAbschlag[]>(
      '/api/invoicing/invoices/anrechenbare-abschlaege',
      { params },
    );
  }

  /**
   * Setzt die angerechneten Abschläge eines Schlussrechnungs-ENTWURFS neu
   * (vollständige Auswahl). Der Server baut die Anrechnungspositionen daraus neu
   * auf und rechnet die Summen nach. 422 nach der Veröffentlichung.
   */
  setInvoiceAdvances(id: string, advanceInvoiceIds: string[]): Observable<InvoiceDetail> {
    return this.http.put<InvoiceDetail>(
      `/api/invoicing/invoices/${id}/advances`,
      { advance_invoice_ids: advanceInvoiceIds },
    );
  }

  /** Beteiligten (Schuldner/Empfänger …) am Rechnungsentwurf ergänzen. */
  addInvoiceParty(id: string, payload: InvoicePartyCreate): Observable<InvoiceDetail> {
    return this.http.post<InvoiceDetail>(`/api/invoicing/invoices/${id}/parties`, payload);
  }

  /** Rechnung veröffentlichen — unumkehrbar: DB vergibt die Belegnummer,
   *  friert ein und prüft die Freigabe-Tore (422 bei Verstoß). */
  publishInvoice(id: string): Observable<InvoiceDetail> {
    return this.http.post<InvoiceDetail>(`/api/invoicing/invoices/${id}/publish`, {});
  }

  /**
   * Veröffentlichte Rechnung als PDF-Anhang per E-Mail versenden. `toAddress`
   * überschreibt die serverseitig ermittelte Empfänger-Adresse. Reine Zustellung
   * (kein Statuswechsel); Fehler (kein Empfänger/Konto, SMTP) kommen als 422.
   */
  sendInvoiceEmail(id: string, toAddress: string): Observable<InvoiceEmailResult> {
    return this.http.post<InvoiceEmailResult>(
      `/api/invoicing/invoices/${id}/send-email`,
      { to_address: toAddress },
    );
  }

  /**
   * E-Rechnung (ZUGFeRD/Factur-X): Hybrid-PDF (PDF/A-3 mit eingebettetem
   * CII-XML) einer veröffentlichten Rechnung.
   *
   * Bewusst als Blob über den HttpClient und NICHT als `window.open`/Direkt-URL:
   * nur so gehen Session-Cookie und CSRF-Header durch den Interceptor. Lässt die
   * Datenlage kein gültiges EN16931-XML zu (kein Firmenprofil, kein Empfänger),
   * antwortet der Server mit 422 — der Fehlerkörper ist dann ein Blob, den der
   * Aufrufer als Text liest (Muster wie beim DATEV-Export).
   */
  zugferdPdf(id: string): Observable<Blob> {
    return this.http.get(`/api/invoicing/invoices/${id}/zugferd.pdf`, {
      responseType: 'blob',
    });
  }

  // --- Abrechnung (Migration 0084) -----------------------------------------

  /**
   * Rechnung (ENTWURF) aus einem Angebot — die **Angebotskopie** (PAUSCHAL).
   * Positionen werden wertgleich kopiert; Alternativ-/Bedarfspositionen bleiben
   * draußen. Ein zweiter Lauf über dasselbe Angebot scheitert (422).
   */
  rechnungAusAngebot(payload: RechnungAusAngebot): Observable<InvoiceDetail> {
    return this.http.post<InvoiceDetail>('/api/invoicing/invoices/aus-angebot', payload);
  }

  /**
   * Rechnung (ENTWURF) aus **Bericht + Zeiten** eines REGIE-Auftrags.
   *
   * Fehlt ein Preis, antwortet der Server mit **422 und `preis_unbekannt`** — kein
   * Fehlerbalken, sondern eine Klärungsaufgabe (siehe `PreisKlaerungFehler`). Der
   * Aufrufer nennt die Einzelpreise in `preise` und ruft erneut auf.
   */
  rechnungAusAuftrag(payload: RechnungAusAuftrag): Observable<InvoiceDetail> {
    return this.http.post<InvoiceDetail>('/api/invoicing/invoices/aus-auftrag', payload);
  }

  /**
   * Rechnung (ENTWURF) über die **Abweichungen** eines PAUSCHAL-Auftrags.
   *
   * Nur die Mehrmenge (MEHRVERBRAUCH) bzw. die volle Menge einer Zusatzleistung
   * (ZUSATZ) — die pauschal vereinbarte Leistung steht schon auf der
   * Angebotsrechnung. Fehlt ein Preis: **422 mit `preis_unbekannt`** (derselbe
   * Klärungsweg wie beim Regielauf), niemals eine Position über 0,00 €.
   */
  rechnungAusNachtrag(payload: RechnungAusNachtrag): Observable<InvoiceDetail> {
    return this.http.post<InvoiceDetail>('/api/invoicing/invoices/aus-nachtrag', payload);
  }

  /**
   * Der Ausgang eines versendeten Angebots (angenommen | abgelehnt | abgelaufen).
   *
   * Ändert **nur** den Status: Snapshot und Inhalts-Hash des versendeten Angebots
   * bleiben unangetastet (B-30).
   */
  setQuoteStatus(id: string, to_status: QuoteAusgang): Observable<QuoteDetail> {
    return this.http.post<QuoteDetail>(`${this.base}/${id}/status`, { to_status });
  }

  /**
   * Notausgang für einen verunglückten Entwurf: löst die Abrechnungsbindungen und
   * entfernt die gebundenen Positionen aus ihm. Die Quellen werden wieder
   * abrechenbar — und zwar WEIL der Entwurf sie nicht mehr in Rechnung stellt.
   *
   * Recht **invoicing/STORNIEREN**, Begründung Pflicht. Eine veröffentlichte
   * Rechnung wird nicht entbunden, sondern storniert (422).
   */
  bindungenLoesen(id: string, reason: string): Observable<InvoiceDetail> {
    return this.http.post<InvoiceDetail>(
      `/api/invoicing/invoices/${id}/bindungen-loesen`,
      { reason },
    );
  }

  /**
   * Hängt EINE Position an einen Rechnungsentwurf an (ans Ende).
   *
   * Der Weg, einen **gebundenen** Entwurf zu ergänzen (Anfahrt, Rabatt, Text):
   * Der Beleg-Editor ersetzt den ganzen Positionssatz per Delete+Insert und läuft
   * damit gegen die gebundene Zeile (422) — das INSERT einer neuen Zeile lässt die
   * DB dagegen ausdrücklich zu (Migration 0088).
   */
  addInvoiceLine(id: string, line: QuoteLineInput): Observable<InvoiceDetail> {
    return this.http.post<InvoiceDetail>(`/api/invoicing/invoices/${id}/lines`, line);
  }

  /**
   * Entfernt die **letzte** Position eines Entwurfs — nur, wenn sie ungebunden ist
   * (sonst 422). Die Rücknahme einer gerade angehängten Zeile; jede andere zu
   * entfernen hieße umnummerieren, und das wäre ein UPDATE auf gebundene Zeilen.
   */
  removeLastInvoiceLine(id: string): Observable<InvoiceDetail> {
    return this.http.delete<InvoiceDetail>(`/api/invoicing/invoices/${id}/lines/last`);
  }
}
