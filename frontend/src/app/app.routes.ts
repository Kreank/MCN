import { Routes } from '@angular/router';

import { authGuard, darfGuard } from './core/auth.guard';

export const routes: Routes = [
  {
    path: 'login',
    title: 'Anmeldung — MCN Leitstand',
    loadComponent: () => import('./features/login/login').then((m) => m.Login),
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
        path: 'kein-zugriff',
        title: 'Kein Zugriff — MCN Leitstand',
        loadComponent: () =>
          import('./shared/kein-zugriff/kein-zugriff').then((m) => m.KeinZugriff),
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
        path: 'buchhaltung/:id',
        title: 'Rechnung — MCN Leitstand',
        canActivate: [darfGuard('invoicing', 'LESEN')],
        loadComponent: () =>
          import('./features/buchhaltung-detail/buchhaltung-detail').then(
            (m) => m.BuchhaltungDetail,
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
      { path: '**', redirectTo: 'uebersicht' },
    ],
  },
];
