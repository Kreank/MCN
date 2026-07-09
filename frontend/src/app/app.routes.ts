import { Routes } from '@angular/router';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'uebersicht' },
  {
    path: 'kontakte',
    title: 'Kontakte — MCN Leitstand',
    loadComponent: () => import('./features/kontakte/kontakte').then((m) => m.Kontakte),
  },
  {
    path: 'kontakte/:id',
    title: 'Kontakt — MCN Leitstand',
    loadComponent: () =>
      import('./features/kontakt-detail/kontakt-detail').then((m) => m.KontaktDetail),
  },
  {
    path: 'uebersicht',
    title: 'Übersicht — MCN Leitstand',
    loadComponent: () => import('./features/uebersicht/uebersicht').then((m) => m.Uebersicht),
  },
  {
    path: 'liegenschaften',
    title: 'Liegenschaften — MCN Leitstand',
    loadComponent: () =>
      import('./features/liegenschaften/liegenschaften').then((m) => m.Liegenschaften),
  },
  {
    path: 'liegenschaften/:id',
    title: 'Liegenschaft — MCN Leitstand',
    loadComponent: () =>
      import('./features/liegenschaft-detail/liegenschaft-detail').then(
        (m) => m.LiegenschaftDetail,
      ),
  },
  {
    path: 'projekte',
    title: 'Projekte — MCN Leitstand',
    loadComponent: () => import('./features/projekte/projekte').then((m) => m.Projekte),
  },
  {
    path: 'projekte/:id',
    title: 'Projekt — MCN Leitstand',
    loadComponent: () =>
      import('./features/projekt-detail/projekt-detail').then((m) => m.ProjektDetail),
  },
  {
    path: 'vorgaenge/:id',
    title: 'Vorgang — MCN Leitstand',
    loadComponent: () =>
      import('./features/vorgang-detail/vorgang-detail').then((m) => m.VorgangDetail),
  },
  {
    path: 'auftraege/:id',
    title: 'Auftrag — MCN Leitstand',
    loadComponent: () =>
      import('./features/auftrag-detail/auftrag-detail').then((m) => m.AuftragDetail),
  },
  {
    path: 'planung',
    pathMatch: 'full',
    title: 'Planung — MCN Leitstand',
    loadComponent: () => import('./features/einsaetze/einsaetze').then((m) => m.Einsaetze),
  },
  {
    path: 'planung/plantafel',
    title: 'Plantafel — MCN Leitstand',
    loadComponent: () => import('./features/plantafel/plantafel').then((m) => m.Plantafel),
  },
  {
    path: 'planung/kalender',
    title: 'Kalender — MCN Leitstand',
    loadComponent: () =>
      import('./features/planung-kalender/planung-kalender').then((m) => m.PlanungKalender),
  },
  {
    path: 'planung/:id',
    title: 'Einsatz — MCN Leitstand',
    loadComponent: () =>
      import('./features/einsatz-detail/einsatz-detail').then((m) => m.EinsatzDetail),
  },
  {
    path: 'dokumente',
    title: 'Dokumente — MCN Leitstand',
    loadComponent: () => import('./features/dokumente/dokumente').then((m) => m.Dokumente),
  },
  {
    path: 'dokumente/:id',
    title: 'Beleg — MCN Leitstand',
    loadComponent: () =>
      import('./features/beleg-detail/beleg-detail').then((m) => m.BelegDetail),
  },
  {
    path: 'rechnungen/:id',
    title: 'Rechnung — MCN Leitstand',
    loadComponent: () =>
      import('./features/rechnung-detail/rechnung-detail').then((m) => m.RechnungDetail),
  },
  {
    path: 'aufgaben',
    title: 'Aufgaben — MCN Leitstand',
    loadComponent: () => import('./features/aufgaben/aufgaben').then((m) => m.Aufgaben),
  },
  {
    path: 'wartung',
    pathMatch: 'full',
    title: 'Wartung — MCN Leitstand',
    loadComponent: () => import('./features/wartung/wartung').then((m) => m.Wartung),
  },
  {
    path: 'wartung/:id',
    title: 'Wartungsvertrag — MCN Leitstand',
    loadComponent: () =>
      import('./features/wartung-detail/wartung-detail').then((m) => m.WartungDetail),
  },
  {
    path: 'buchhaltung',
    pathMatch: 'full',
    title: 'Buchhaltung — MCN Leitstand',
    loadComponent: () =>
      import('./features/buchhaltung/buchhaltung').then((m) => m.Buchhaltung),
  },
  {
    path: 'buchhaltung/mahnwesen',
    title: 'Mahnwesen — MCN Leitstand',
    loadComponent: () =>
      import('./features/mahnwesen/mahnwesen').then((m) => m.Mahnwesen),
  },
  {
    path: 'buchhaltung/:id',
    title: 'Rechnung — MCN Leitstand',
    loadComponent: () =>
      import('./features/buchhaltung-detail/buchhaltung-detail').then(
        (m) => m.BuchhaltungDetail,
      ),
  },
  {
    path: 'auswertungen',
    pathMatch: 'full',
    title: 'Auswertungen — MCN Leitstand',
    loadComponent: () =>
      import('./features/auswertungen/auswertungen').then((m) => m.Auswertungen),
  },
  {
    path: 'auswertungen/umsatz-projektuebersicht',
    title: 'Umsatz- und Projektübersicht — MCN Leitstand',
    loadComponent: () =>
      import('./features/auswertungen-umsatz/auswertungen-umsatz').then(
        (m) => m.AuswertungenUmsatz,
      ),
  },
  {
    path: 'auswertungen/kunden',
    title: 'Kunden — MCN Leitstand',
    loadComponent: () =>
      import('./features/auswertungen-kunden/auswertungen-kunden').then(
        (m) => m.AuswertungenKunden,
      ),
  },
  {
    path: 'artikel',
    title: 'Artikel & Leistungen — MCN Leitstand',
    loadComponent: () => import('./features/artikel/artikel').then((m) => m.Artikel),
  },
  {
    path: 'artikel/:id',
    title: 'Artikel — MCN Leitstand',
    loadComponent: () =>
      import('./features/artikel-detail/artikel-detail').then((m) => m.ArtikelDetail),
  },
  {
    path: 'leistungen/:id',
    title: 'Leistung — MCN Leitstand',
    loadComponent: () =>
      import('./features/leistung-detail/leistung-detail').then((m) => m.LeistungDetail),
  },
  { path: '**', redirectTo: 'uebersicht' },
];
