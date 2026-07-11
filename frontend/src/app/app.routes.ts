import { Routes } from '@angular/router';

import { authGuard, darfGuard } from './core/auth.guard';

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
        path: 'kein-zugriff',
        title: 'Kein Zugriff — MCN Leitstand',
        loadComponent: () =>
          import('./shared/kein-zugriff/kein-zugriff').then((m) => m.KeinZugriff),
      },
      {
        // Schnelleinstieg „Meldung erfassen" — legt Person + Liegenschaft +
        // Vorgang atomar an. Der Server gatet zusätzlich identity/property; der
        // Route-Guard spiegelt das primäre Recht (Vorgangsanlage).
        path: 'schnellerfassung',
        title: 'Meldung erfassen — MCN Leitstand',
        canActivate: [darfGuard('workflow', 'ANLEGEN')],
        loadComponent: () =>
          import('./features/schnellerfassung/schnellerfassung').then((m) => m.Schnellerfassung),
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
        path: 'planung',
        pathMatch: 'full',
        title: 'Planung — MCN Leitstand',
        canActivate: [darfGuard('workflow', 'LESEN')],
        loadComponent: () => import('./features/einsaetze/einsaetze').then((m) => m.Einsaetze),
      },
      {
        path: 'planung/plantafel',
        title: 'Plantafel — MCN Leitstand',
        canActivate: [darfGuard('workflow', 'LESEN')],
        loadComponent: () => import('./features/plantafel/plantafel').then((m) => m.Plantafel),
      },
      {
        path: 'planung/kalender',
        title: 'Kalender — MCN Leitstand',
        canActivate: [darfGuard('workflow', 'LESEN')],
        loadComponent: () =>
          import('./features/planung-kalender/planung-kalender').then((m) => m.PlanungKalender),
      },
      {
        path: 'planung/einstellungen',
        title: 'Kategorien & Ressourcen — MCN Leitstand',
        canActivate: [darfGuard('workflow', 'LESEN')],
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
        // Angebotseditor. MUSS vor 'dokumente/:id' stehen, sonst schluckt der
        // Parameter das statische Segment 'angebot'.
        path: 'dokumente/angebot/:id',
        title: 'Angebot bearbeiten — MCN Leitstand',
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
        canActivate: [darfGuard('invoicing', 'LESEN')],
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
        canActivate: [darfGuard('hr', 'LESEN')],
        loadComponent: () =>
          import('./features/mitarbeiter/mitarbeiter').then((m) => m.Mitarbeiter),
      },
      {
        path: 'mitarbeiter/:id',
        title: 'Mitarbeiter — MCN Leitstand',
        canActivate: [darfGuard('hr', 'LESEN')],
        loadComponent: () =>
          import('./features/mitarbeiter-detail/mitarbeiter-detail').then(
            (m) => m.MitarbeiterDetail,
          ),
      },
      {
        path: 'wartung',
        pathMatch: 'full',
        title: 'Wartung — MCN Leitstand',
        canActivate: [darfGuard('workflow', 'LESEN')],
        loadComponent: () => import('./features/wartung/wartung').then((m) => m.Wartung),
      },
      {
        path: 'wartung/:id',
        title: 'Wartungsvertrag — MCN Leitstand',
        canActivate: [darfGuard('workflow', 'LESEN')],
        loadComponent: () =>
          import('./features/wartung-detail/wartung-detail').then((m) => m.WartungDetail),
      },
      {
        path: 'buchhaltung',
        pathMatch: 'full',
        title: 'Buchhaltung — MCN Leitstand',
        canActivate: [darfGuard('invoicing', 'LESEN')],
        loadComponent: () =>
          import('./features/buchhaltung/buchhaltung').then((m) => m.Buchhaltung),
      },
      {
        path: 'buchhaltung/mahnwesen',
        title: 'Mahnwesen — MCN Leitstand',
        canActivate: [darfGuard('invoicing', 'LESEN')],
        loadComponent: () => import('./features/mahnwesen/mahnwesen').then((m) => m.Mahnwesen),
      },
      {
        // Mahnlauf (Stapel). MUSS vor 'buchhaltung/:id' stehen, sonst schluckt der
        // Parameter das statische Segment 'mahnlauf'.
        path: 'buchhaltung/mahnlauf',
        title: 'Mahnlauf — MCN Leitstand',
        canActivate: [darfGuard('invoicing', 'LESEN')],
        loadComponent: () => import('./features/mahnlauf/mahnlauf').then((m) => m.Mahnlauf),
      },
      {
        path: 'buchhaltung/:id',
        title: 'Rechnung — MCN Leitstand',
        canActivate: [darfGuard('invoicing', 'LESEN')],
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
        canActivate: [darfGuard('invoicing', 'LESEN')],
        loadComponent: () =>
          import('./features/auswertungen/auswertungen').then((m) => m.Auswertungen),
      },
      {
        path: 'auswertungen/umsatz-projektuebersicht',
        title: 'Umsatz- und Projektübersicht — MCN Leitstand',
        canActivate: [darfGuard('invoicing', 'LESEN')],
        loadComponent: () =>
          import('./features/auswertungen-umsatz/auswertungen-umsatz').then(
            (m) => m.AuswertungenUmsatz,
          ),
      },
      {
        path: 'auswertungen/kunden',
        title: 'Kunden — MCN Leitstand',
        canActivate: [darfGuard('invoicing', 'LESEN')],
        loadComponent: () =>
          import('./features/auswertungen-kunden/auswertungen-kunden').then(
            (m) => m.AuswertungenKunden,
          ),
      },
      {
        path: 'auswertungen/projekte',
        title: 'Projekte-Auswertung — MCN Leitstand',
        canActivate: [darfGuard('invoicing', 'LESEN')],
        loadComponent: () =>
          import('./features/auswertungen-projekte/auswertungen-projekte').then(
            (m) => m.AuswertungenProjekte,
          ),
      },
      {
        path: 'auswertungen/artikel',
        title: 'Artikel-Auswertung — MCN Leitstand',
        canActivate: [darfGuard('invoicing', 'LESEN')],
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
        canActivate: [darfGuard('hr', 'LESEN')],
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
        canActivate: [darfGuard('invoicing', 'LESEN')],
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
