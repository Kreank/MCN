import { Routes } from '@angular/router';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'kontakte' },
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
    loadComponent: () => import('./shared/platzhalter/platzhalter').then((m) => m.Platzhalter),
    data: {
      platzhalter: {
        titel: 'Übersicht',
        text: 'Der Leitstand bündelt hier bald offene Vorgänge, fällige Einsätze und KI-Vorschläge auf einen Blick. Bis dahin startest du direkt im Kontaktregister.',
      },
    },
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
    path: 'aufgaben',
    title: 'Aufgaben — MCN Leitstand',
    loadComponent: () => import('./features/aufgaben/aufgaben').then((m) => m.Aufgaben),
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
  { path: '**', redirectTo: 'kontakte' },
];
