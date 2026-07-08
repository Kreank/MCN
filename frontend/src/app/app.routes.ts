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
    loadComponent: () => import('./shared/platzhalter/platzhalter').then((m) => m.Platzhalter),
    data: {
      platzhalter: {
        titel: 'Projekte',
        text: 'Projekte, Aufträge, Einsätze und ihre Statusautomaten laufen künftig an dieser Stelle zusammen. Noch ist der Bereich leer.',
      },
    },
  },
  {
    path: 'dokumente',
    title: 'Dokumente — MCN Leitstand',
    loadComponent: () => import('./shared/platzhalter/platzhalter').then((m) => m.Platzhalter),
    data: {
      platzhalter: {
        titel: 'Dokumente',
        text: 'Angebote und Rechnungen (GoBD-relevant) erhalten hier ihren geführten Platz — mit Editor und Konfigurator. Der Bereich folgt.',
      },
    },
  },
  { path: '**', redirectTo: 'kontakte' },
];
