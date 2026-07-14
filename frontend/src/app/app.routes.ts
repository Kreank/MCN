import { Routes } from '@angular/router';

import { authGuard, darfAlleGuard, darfGuard } from './core/auth.guard';

export const routes: Routes = [
  {
    path: 'login',
    title: 'Anmeldung — MCN Leitstand',
    loadComponent: () => import('./features/login/login').then((m) => m.Login),
  },
  // Passwort-Reset — anmeldefrei (wie /login), erreichbar ohne Sitzung.
  {
    path: 'passwort-vergessen',
    title: 'Passwort vergessen — MCN Leitstand',
    loadComponent: () =>
      import('./features/passwort-reset/passwort-vergessen').then((m) => m.PasswortVergessen),
  },
  {
    path: 'passwort-zuruecksetzen',
    title: 'Passwort zurücksetzen — MCN Leitstand',
    loadComponent: () =>
      import('./features/passwort-reset/passwort-zuruecksetzen').then(
        (m) => m.PasswortZuruecksetzen,
      ),
  },
  // Alles Übrige ist anmeldepflichtig — ein Wächter am gemeinsamen Elternknoten.
  // Bereiche mit Modulrecht tragen zusätzlich einen darfGuard (spiegelt die
  // Server-Durchsetzung; verhindert Zugriff per direkter URL/returnUrl).
  {
    path: '',
    canActivateChild: [authGuard],
    children: [
      { path: '', pathMatch: 'full', redirectTo: 'uebersicht' },
      {
        path: 'uebersicht',
        title: 'Übersicht — MCN Leitstand',
        loadComponent: () => import('./features/uebersicht/uebersicht').then((m) => m.Uebersicht),
      },
      {
        path: 'profil',
        title: 'Mein Profil — MCN Leitstand',
        loadComponent: () => import('./features/profil/profil').then((m) => m.Profil),
      },
      {
        // Selbstauskunft (eigene HR-Daten). Recht hr/LESEN; der Server liefert
        // nur die eigene Zeile (row_scope EIGENE ist hier zulässig).
        path: 'meine-personalakte',
        title: 'Meine Personalakte — MCN Leitstand',
        canActivate: [darfGuard('hr', 'LESEN')],
        loadComponent: () =>
          import('./features/meine-personalakte/meine-personalakte').then(
            (m) => m.MeinePersonalakte,
          ),
      },
      {
        // Die Stempeluhr. Recht hr/AENDERN — row_scope EIGENE ist hier genau
        // richtig: der Server bucht immer auf den Akteur und nimmt gar keine
        // fremde user_id entgegen.
        path: 'meine-zeiten',
        title: 'Meine Zeiten — MCN Leitstand',
        canActivate: [darfGuard('hr', 'AENDERN')],
        loadComponent: () =>
          import('./features/meine-zeiten/meine-zeiten').then((m) => m.MeineZeiten),
      },
      {
        // Verwaltungssicht der Zeiterfassung. `darfAlleGuard`: Der Server verweigert
        // row_scope EIGENE (403) — der Monteur stempelt unter „Meine Zeiten". Eine
        // Route, die zwangsläufig auf „Kein Zugriff" führt, wird gar nicht erst
        // geöffnet (die Navigation blendet den Punkt ohnehin aus).
        path: 'zeiterfassung',
        title: 'Zeiterfassung — MCN Leitstand',
        canActivate: [darfAlleGuard('hr', 'LESEN')],
        loadComponent: () =>
          import('./features/zeiterfassung/zeiterfassung').then((m) => m.Zeiterfassung),
      },
      {
        path: 'kein-zugriff',
        title: 'Kein Zugriff — MCN Leitstand',
        loadComponent: () =>
          import('./shared/kein-zugriff/kein-zugriff').then((m) => m.KeinZugriff),
      },
      {
        // Schnelleinstieg „Meldung erfassen" — legt Person + Liegenschaft +
        // Vorgang atomar an. `POST /workflow/quick-intake` ist fail-closed
        // (`require` auf identity/property/workflow) — ein Konto mit row_scope
        // EIGENE bekommt 403. Der Header-CTA blendet sich für den Monteur ohnehin
        // aus; der Guard darf die Direkt-URL nicht offenlassen.
        path: 'schnellerfassung',
        title: 'Meldung erfassen — MCN Leitstand',
        canActivate: [darfAlleGuard('workflow', 'ANLEGEN')],
        loadComponent: () =>
          import('./features/schnellerfassung/schnellerfassung').then((m) => m.Schnellerfassung),
      },
      // Werkzeuge (Rechner). Bewusst OHNE darfGuard: die Rechner sprechen mit
      // keinem Server und lesen keine Fachdaten — jede angemeldete Rolle darf
      // rechnen. Der Query-Parameter `objekt` trägt einen Kontext (Liegenschaft)
      // auf die Ausgabe. Die parameterlose Route zeigt das erste Werkzeug.
      {
        path: 'werkzeuge',
        pathMatch: 'full',
        title: 'Werkzeuge — MCN Leitstand',
        loadComponent: () => import('./features/werkzeuge/werkzeuge').then((m) => m.Werkzeuge),
      },
      {
        path: 'werkzeuge/:werkzeug',
        title: 'Werkzeuge — MCN Leitstand',
        loadComponent: () => import('./features/werkzeuge/werkzeuge').then((m) => m.Werkzeuge),
      },
      {
        path: 'kontakte',
        title: 'Kontakte — MCN Leitstand',
        canActivate: [darfGuard('identity', 'LESEN')],
        loadComponent: () => import('./features/kontakte/kontakte').then((m) => m.Kontakte),
      },
      {
        path: 'kontakte/:id',
        title: 'Kontakt — MCN Leitstand',
        canActivate: [darfGuard('identity', 'LESEN')],
        loadComponent: () =>
          import('./features/kontakt-detail/kontakt-detail').then((m) => m.KontaktDetail),
      },
      {
        path: 'liegenschaften',
        title: 'Liegenschaften — MCN Leitstand',
        canActivate: [darfGuard('property', 'LESEN')],
        loadComponent: () =>
          import('./features/liegenschaften/liegenschaften').then((m) => m.Liegenschaften),
      },
      {
        path: 'liegenschaften/:id',
        title: 'Liegenschaft — MCN Leitstand',
        canActivate: [darfGuard('property', 'LESEN')],
        loadComponent: () =>
          import('./features/liegenschaft-detail/liegenschaft-detail').then(
            (m) => m.LiegenschaftDetail,
          ),
      },
      {
        // Anlagenmappe. EIGENE Route (kein Ausklapp-Panel im Liegenschaftsreiter):
        // Aufträge, Prüfungen und Fälligkeiten hängen an der Anlage und müssen
        // umgekehrt auf sie verlinken können — ein Panel hat keine Adresse.
        path: 'anlagen/:id',
        title: 'Technische Anlage — MCN Leitstand',
        canActivate: [darfGuard('property', 'LESEN')],
        loadComponent: () =>
          import('./features/anlage-detail/anlage-detail').then((m) => m.AnlageDetail),
      },
      {
        path: 'projekte',
        title: 'Projekte — MCN Leitstand',
        canActivate: [darfGuard('workflow', 'LESEN')],
        loadComponent: () => import('./features/projekte/projekte').then((m) => m.Projekte),
      },
      {
        // Vorgangs-Kanban. MUSS vor 'projekte/:id' stehen, sonst schluckt der
        // Parameter das statische Segment 'kanban'.
        path: 'projekte/kanban',
        title: 'Vorgang-Board — MCN Leitstand',
        canActivate: [darfGuard('workflow', 'LESEN')],
        loadComponent: () =>
          import('./features/vorgang-kanban/vorgang-kanban').then((m) => m.VorgangKanban),
      },
      {
        path: 'projekte/:id',
        title: 'Projekt — MCN Leitstand',
        canActivate: [darfGuard('workflow', 'LESEN')],
        loadComponent: () =>
          import('./features/projekt-detail/projekt-detail').then((m) => m.ProjektDetail),
      },
      {
        path: 'vorgaenge/:id',
        title: 'Vorgang — MCN Leitstand',
        canActivate: [darfGuard('workflow', 'LESEN')],
        loadComponent: () =>
          import('./features/vorgang-detail/vorgang-detail').then((m) => m.VorgangDetail),
      },
      {
        path: 'auftraege/:id',
        title: 'Auftrag — MCN Leitstand',
        canActivate: [darfGuard('workflow', 'LESEN')],
        loadComponent: () =>
          import('./features/auftrag-detail/auftrag-detail').then((m) => m.AuftragDetail),
      },
      {
        // Entitäts-Dossier: alles zu EINER Entität in EINEM Aufruf.
        // BEWUSST OHNE `darfGuard`: das Kernrecht hängt an der Dossier-Art
        // (kontakt→identity, liegenschaft→property, projekt/auftrag→workflow)
        // und steht erst im Routenparameter — ein statischer Guard könnte hier
        // nur raten. Der Server tort hart (403); die Komponente zeigt dann
        // „Kein Zugriff". Dieselbe Linie wie bei /zeiterfassung.
        path: 'dossier/:typ/:id',
        title: 'Dossier — MCN Leitstand',
        loadComponent: () => import('./features/dossier/dossier').then((m) => m.Dossier),
      },
      {
        path: 'planung',
        pathMatch: 'full',
        title: 'Planung — MCN Leitstand',
        canActivate: [darfGuard('workflow', 'LESEN')],
        loadComponent: () => import('./features/einsaetze/einsaetze').then((m) => m.Einsaetze),
      },
      // Die vier Dispositionssichten der Planung: `darfAlleGuard`.
      // Plantafel, Kalender, „Wer fehlt?" und die Kategorien-/Ressourcenpflege
      // stehen auf fail-closed-Endpunkten (`planung.py` nutzt dort `require`) —
      // ein Konto mit row_scope EIGENE bekommt 403. Sie zeigen fremde Termine und
      // fremde Abwesenheiten; die Einsatzliste (`planung`, `require_scoped`)
      // bleibt für den Monteur offen und zeigt ihm seine eigenen Einsätze.
      {
        path: 'planung/plantafel',
        title: 'Plantafel — MCN Leitstand',
        canActivate: [darfAlleGuard('workflow', 'LESEN')],
        loadComponent: () => import('./features/plantafel/plantafel').then((m) => m.Plantafel),
      },
      {
        path: 'planung/kalender',
        title: 'Kalender — MCN Leitstand',
        canActivate: [darfAlleGuard('workflow', 'LESEN')],
        loadComponent: () =>
          import('./features/planung-kalender/planung-kalender').then((m) => m.PlanungKalender),
      },
      {
        // „Wer ist gerade nicht da" — ohne Abwesenheitsart (DSGVO Art. 9).
        // Bewusst am workflow-Recht: die Disposition darf das ohne `hr`.
        path: 'planung/abwesend',
        title: 'Wer fehlt? — MCN Leitstand',
        canActivate: [darfAlleGuard('workflow', 'LESEN')],
        loadComponent: () =>
          import('./features/planung-abwesend/planung-abwesend').then(
            (m) => m.PlanungAbwesend,
          ),
      },
      {
        path: 'planung/einstellungen',
        title: 'Kategorien & Ressourcen — MCN Leitstand',
        canActivate: [darfAlleGuard('workflow', 'LESEN')],
        loadComponent: () =>
          import('./features/planung-einstellungen/planung-einstellungen').then(
            (m) => m.PlanungEinstellungen,
          ),
      },
      {
        path: 'planung/:id',
        title: 'Einsatz — MCN Leitstand',
        canActivate: [darfGuard('workflow', 'LESEN')],
        loadComponent: () =>
          import('./features/einsatz-detail/einsatz-detail').then((m) => m.EinsatzDetail),
      },
      {
        path: 'dokumente',
        title: 'Dokumente — MCN Leitstand',
        canActivate: [darfGuard('invoicing', 'LESEN')],
        loadComponent: () => import('./features/dokumente/dokumente').then((m) => m.Dokumente),
      },
      {
        // Beleg-Editor (Angebot). MUSS vor 'dokumente/:id' stehen, sonst schluckt
        // der Parameter das statische Segment 'angebot'.
        path: 'dokumente/angebot/:id',
        title: 'Angebot bearbeiten — MCN Leitstand',
        data: { belegArt: 'angebot' },
        canActivate: [darfGuard('invoicing', 'AENDERN')],
        loadComponent: () =>
          import('./features/angebot-editor/angebot-editor').then((m) => m.AngebotEditor),
      },
      {
        // Derselbe Editor im Rechnungs-Modus (Artikel-Palette für Rechnungen).
        path: 'dokumente/rechnung/:id',
        title: 'Rechnung bearbeiten — MCN Leitstand',
        data: { belegArt: 'rechnung' },
        canActivate: [darfGuard('invoicing', 'AENDERN')],
        loadComponent: () =>
          import('./features/angebot-editor/angebot-editor').then((m) => m.AngebotEditor),
      },
      {
        path: 'dokumente/:id',
        title: 'Beleg — MCN Leitstand',
        canActivate: [darfGuard('invoicing', 'LESEN')],
        loadComponent: () =>
          import('./features/beleg-detail/beleg-detail').then((m) => m.BelegDetail),
      },
      {
        path: 'rechnungen/:id',
        title: 'Rechnung — MCN Leitstand',
        canActivate: [darfAlleGuard('invoicing', 'LESEN')],
        loadComponent: () =>
          import('./features/rechnung-detail/rechnung-detail').then((m) => m.RechnungDetail),
      },
      {
        path: 'aufgaben',
        title: 'Aufgaben — MCN Leitstand',
        canActivate: [darfGuard('workflow', 'LESEN')],
        loadComponent: () => import('./features/aufgaben/aufgaben').then((m) => m.Aufgaben),
      },
      {
        // Vier-Augen-Anträge. Die Liste verlangt nur security/LESEN; Genehmigen
        // und Ablehnen gatet der Server zusätzlich mit security/FREIGEBEN.
        path: 'freigaben',
        title: 'Freigaben — MCN Leitstand',
        canActivate: [darfGuard('security', 'LESEN')],
        loadComponent: () => import('./features/freigaben/freigaben').then((m) => m.Freigaben),
      },
      {
        path: 'mitarbeiter',
        pathMatch: 'full',
        title: 'Mitarbeiter — MCN Leitstand',
        canActivate: [darfAlleGuard('hr', 'LESEN')],
        loadComponent: () =>
          import('./features/mitarbeiter/mitarbeiter').then((m) => m.Mitarbeiter),
      },
      {
        path: 'mitarbeiter/:id',
        title: 'Mitarbeiter — MCN Leitstand',
        canActivate: [darfAlleGuard('hr', 'LESEN')],
        loadComponent: () =>
          import('./features/mitarbeiter-detail/mitarbeiter-detail').then(
            (m) => m.MitarbeiterDetail,
          ),
      },
      // Wartung: Einstieg ist die Fälligkeiten-Ansicht („Was steht an?"),
      // nicht mehr die Vertragsliste — die Frage des Alltags ist „was ist zu
      // tun?", nicht „welche Verträge gibt es?". Rechtemodul ist seit Migration
      // 0071 `maintenance` (vorher lief die Wartung auf `workflow` mit).
      // WICHTIG: Die literalen Unterrouten stehen VOR 'wartung/:id', sonst
      // schluckte der Parameter-Match sie.
      {
        path: 'wartung',
        pathMatch: 'full',
        title: 'Fälligkeiten — MCN Leitstand',
        canActivate: [darfGuard('maintenance', 'LESEN')],
        loadComponent: () =>
          import('./features/faelligkeiten/faelligkeiten').then((m) => m.Faelligkeiten),
      },
      {
        path: 'wartung/vertraege',
        pathMatch: 'full',
        title: 'Wartungsverträge — MCN Leitstand',
        canActivate: [darfGuard('maintenance', 'LESEN')],
        loadComponent: () => import('./features/wartung/wartung').then((m) => m.Wartung),
      },
      {
        path: 'wartung/pruefungen',
        pathMatch: 'full',
        title: 'Prüffristen — MCN Leitstand',
        canActivate: [darfGuard('maintenance', 'LESEN')],
        loadComponent: () =>
          import('./features/pruefungen/pruefungen').then((m) => m.Pruefungen),
      },
      {
        path: 'wartung/gewaehrleistung',
        pathMatch: 'full',
        title: 'Gewährleistung — MCN Leitstand',
        canActivate: [darfGuard('maintenance', 'LESEN')],
        loadComponent: () =>
          import('./features/gewaehrleistung/gewaehrleistung').then(
            (m) => m.Gewaehrleistung,
          ),
      },
      {
        path: 'wartung/:id',
        title: 'Wartungsvertrag — MCN Leitstand',
        canActivate: [darfGuard('maintenance', 'LESEN')],
        loadComponent: () =>
          import('./features/wartung-detail/wartung-detail').then((m) => m.WartungDetail),
      },
      {
        path: 'buchhaltung',
        pathMatch: 'full',
        title: 'Buchhaltung — MCN Leitstand',
        canActivate: [darfAlleGuard('invoicing', 'LESEN')],
        loadComponent: () =>
          import('./features/buchhaltung/buchhaltung').then((m) => m.Buchhaltung),
      },
      {
        path: 'buchhaltung/mahnwesen',
        title: 'Mahnwesen — MCN Leitstand',
        canActivate: [darfAlleGuard('invoicing', 'LESEN')],
        loadComponent: () => import('./features/mahnwesen/mahnwesen').then((m) => m.Mahnwesen),
      },
      {
        // Mahnlauf (Stapel). MUSS vor 'buchhaltung/:id' stehen, sonst schluckt der
        // Parameter das statische Segment 'mahnlauf'.
        path: 'buchhaltung/mahnlauf',
        title: 'Mahnlauf — MCN Leitstand',
        canActivate: [darfAlleGuard('invoicing', 'LESEN')],
        loadComponent: () => import('./features/mahnlauf/mahnlauf').then((m) => m.Mahnlauf),
      },
      {
        path: 'buchhaltung/:id',
        title: 'Rechnung — MCN Leitstand',
        canActivate: [darfAlleGuard('invoicing', 'LESEN')],
        loadComponent: () =>
          import('./features/buchhaltung-detail/buchhaltung-detail').then(
            (m) => m.BuchhaltungDetail,
          ),
      },
      // Belegerfassung (Eingangsrechnungen, accounting.receipt). Die statische
      // Route 'stammdaten' MUSS vor ':id' stehen, sonst schluckt der Parameter sie.
      {
        path: 'belegerfassung',
        pathMatch: 'full',
        title: 'Belegerfassung — MCN Leitstand',
        canActivate: [darfGuard('accounting', 'LESEN')],
        loadComponent: () =>
          import('./features/belegerfassung/belegerfassung').then((m) => m.Belegerfassung),
      },
      {
        path: 'belegerfassung/stammdaten',
        title: 'Kontierung — MCN Leitstand',
        canActivate: [darfGuard('accounting', 'LESEN')],
        loadComponent: () =>
          import('./features/accounting-stammdaten/accounting-stammdaten').then(
            (m) => m.AccountingStammdaten,
          ),
      },
      {
        path: 'belegerfassung/:id',
        title: 'Eingangsbeleg — MCN Leitstand',
        canActivate: [darfGuard('accounting', 'LESEN')],
        loadComponent: () =>
          import('./features/beleg-eingang-detail/beleg-eingang-detail').then(
            (m) => m.BelegEingangDetail,
          ),
      },
      {
        path: 'auswertungen',
        pathMatch: 'full',
        title: 'Auswertungen — MCN Leitstand',
        canActivate: [darfAlleGuard('invoicing', 'LESEN')],
        loadComponent: () =>
          import('./features/auswertungen/auswertungen').then((m) => m.Auswertungen),
      },
      {
        path: 'auswertungen/umsatz-projektuebersicht',
        title: 'Umsatz- und Projektübersicht — MCN Leitstand',
        canActivate: [darfAlleGuard('invoicing', 'LESEN')],
        loadComponent: () =>
          import('./features/auswertungen-umsatz/auswertungen-umsatz').then(
            (m) => m.AuswertungenUmsatz,
          ),
      },
      {
        path: 'auswertungen/kunden',
        title: 'Kunden — MCN Leitstand',
        canActivate: [darfAlleGuard('invoicing', 'LESEN')],
        loadComponent: () =>
          import('./features/auswertungen-kunden/auswertungen-kunden').then(
            (m) => m.AuswertungenKunden,
          ),
      },
      {
        path: 'auswertungen/projekte',
        title: 'Projekte-Auswertung — MCN Leitstand',
        canActivate: [darfAlleGuard('invoicing', 'LESEN')],
        loadComponent: () =>
          import('./features/auswertungen-projekte/auswertungen-projekte').then(
            (m) => m.AuswertungenProjekte,
          ),
      },
      {
        path: 'auswertungen/artikel',
        title: 'Artikel-Auswertung — MCN Leitstand',
        canActivate: [darfAlleGuard('invoicing', 'LESEN')],
        loadComponent: () =>
          import('./features/auswertungen-artikel/auswertungen-artikel').then(
            (m) => m.AuswertungenArtikel,
          ),
      },
      {
        // Personaldaten (DSGVO): eigenes hr-Recht — NUR_LESEN/DISPOSITION kommen
        // nicht rein. Die Landing blendet die Kachel serverseitig entsprechend aus.
        path: 'auswertungen/mitarbeitende',
        title: 'Mitarbeitenden-Auswertung — MCN Leitstand',
        // `darfAlleGuard`: Die Auswertung ist eine Sicht über ALLE Mitarbeitenden;
        // der Endpunkt verweigert row_scope EIGENE mit 403 (MONTEUR trägt hr/LESEN
        // seit 0068 für die eigene Zeiterfassung). Eine Route, die zwangsläufig auf
        // „Kein Zugriff" führt, wird gar nicht erst geöffnet.
        canActivate: [darfAlleGuard('hr', 'LESEN')],
        loadComponent: () =>
          import('./features/auswertungen-mitarbeitende/auswertungen-mitarbeitende').then(
            (m) => m.AuswertungenMitarbeitende,
          ),
      },
      {
        path: 'artikel',
        title: 'Artikel & Leistungen — MCN Leitstand',
        canActivate: [darfGuard('pricing', 'LESEN')],
        loadComponent: () => import('./features/artikel/artikel').then((m) => m.Artikel),
      },
      {
        // Vor 'artikel/:id', sonst fängt der :id-Parameter 'anbindungen' ab.
        path: 'artikel/anbindungen',
        title: 'Lieferanten-Anbindungen — MCN Leitstand',
        canActivate: [darfGuard('pricing', 'LESEN')],
        loadComponent: () =>
          import('./features/haendler-anbindungen/haendler-anbindungen').then(
            (m) => m.HaendlerAnbindungen,
          ),
      },
      {
        // Vor 'artikel/:id', sonst fängt der :id-Parameter 'aufschlagsmatrix' ab.
        path: 'artikel/aufschlagsmatrix',
        title: 'EK→VK-Aufschlagsmatrix — MCN Leitstand',
        canActivate: [darfGuard('pricing', 'LESEN')],
        loadComponent: () =>
          import('./features/aufschlagsmatrix/aufschlagsmatrix').then(
            (m) => m.Aufschlagsmatrix,
          ),
      },
      {
        path: 'artikel/:id',
        title: 'Artikel — MCN Leitstand',
        canActivate: [darfGuard('pricing', 'LESEN')],
        loadComponent: () =>
          import('./features/artikel-detail/artikel-detail').then((m) => m.ArtikelDetail),
      },
      {
        path: 'leistungen/:id',
        title: 'Leistung — MCN Leitstand',
        canActivate: [darfGuard('pricing', 'LESEN')],
        loadComponent: () =>
          import('./features/leistung-detail/leistung-detail').then((m) => m.LeistungDetail),
      },
      // Einstellungen — Read-Ansicht mit LESEN (company/invoicing), Bearbeiten
      // gaten die Komponenten selbst über authService.darf(...,'AENDERN').
      { path: 'einstellungen', pathMatch: 'full', redirectTo: 'einstellungen/profil' },
      {
        path: 'einstellungen/profil',
        title: 'Firmenprofil — MCN Leitstand',
        canActivate: [darfGuard('company', 'LESEN')],
        loadComponent: () =>
          import('./features/firmenprofil/firmenprofil').then((m) => m.Firmenprofil),
      },
      {
        path: 'einstellungen/mahnstufen',
        title: 'Mahnstufen — MCN Leitstand',
        canActivate: [darfAlleGuard('invoicing', 'LESEN')],
        loadComponent: () => import('./features/mahnstufen/mahnstufen').then((m) => m.Mahnstufen),
      },
      {
        path: 'einstellungen/mailversand',
        title: 'Mailversand — MCN Leitstand',
        canActivate: [darfGuard('company', 'LESEN')],
        loadComponent: () =>
          import('./features/mail-einstellungen/mail-einstellungen').then(
            (m) => m.MailEinstellungen,
          ),
      },
      {
        path: 'einstellungen/gewerke',
        title: 'Gewerke — MCN Leitstand',
        canActivate: [darfGuard('company', 'LESEN')],
        loadComponent: () => import('./features/gewerke/gewerke').then((m) => m.Gewerke),
      },
      {
        path: 'einstellungen/niederlassungen',
        title: 'Niederlassungen — MCN Leitstand',
        canActivate: [darfGuard('company', 'LESEN')],
        loadComponent: () =>
          import('./features/niederlassungen/niederlassungen').then((m) => m.Niederlassungen),
      },
      {
        path: 'einstellungen/lohngruppen',
        title: 'Lohngruppen — MCN Leitstand',
        canActivate: [darfGuard('pricing', 'LESEN')],
        loadComponent: () =>
          import('./features/lohngruppen/lohngruppen').then((m) => m.Lohngruppen),
      },
      {
        // Bauteilkatalog (Vorlagen fürs Raumaufmaß). Er hängt am Objektregister,
        // nicht an der Firma: Lesen mit property/LESEN, Anlegen/Ändern gatet der
        // Server (property/ANLEGEN bzw. AENDERN) — die Seite schaltet read-only.
        path: 'einstellungen/bauteilkatalog',
        title: 'Bauteilkatalog — MCN Leitstand',
        canActivate: [darfGuard('property', 'LESEN')],
        loadComponent: () =>
          import('./features/bauteilkatalog/bauteilkatalog').then((m) => m.Bauteilkatalog),
      },
      {
        path: 'einstellungen/akquisekanaele',
        title: 'Akquisekanäle — MCN Leitstand',
        canActivate: [darfGuard('company', 'LESEN')],
        loadComponent: () => import('./features/quellen/quellen').then((m) => m.Quellen),
      },
      {
        // Zeitkategorien + Pausenregel. `hr/LESEN` genügt für die Ansicht;
        // Anlegen/Ändern gatet der Server (hr/ANLEGEN bzw. hr/AENDERN, jeweils
        // row_scope ALLE — `require`, nicht `require_scoped`).
        path: 'einstellungen/zeiterfassung',
        title: 'Zeiterfassung — MCN Leitstand',
        canActivate: [darfGuard('hr', 'LESEN')],
        loadComponent: () =>
          import('./features/zeitkategorien/zeitkategorien').then((m) => m.Zeitkategorien),
      },
      {
        // Rechtematrix & Rollenzuordnungen. Lesen genügt für die Ansicht; das
        // Ändern gatet der Server mit security/AENDERN (UI schaltet read-only).
        path: 'einstellungen/rechte',
        title: 'Rechte & Rollen — MCN Leitstand',
        canActivate: [darfGuard('security', 'LESEN')],
        loadComponent: () =>
          import('./features/rechtematrix/rechtematrix').then((m) => m.Rechtematrix),
      },
      { path: '**', redirectTo: 'uebersicht' },
    ],
  },
];
