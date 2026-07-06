import { Component, inject } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { toSignal } from '@angular/core/rxjs-interop';
import { map } from 'rxjs';

interface PlatzhalterData {
  titel: string;
  text: string;
}

/** Gestaltete "Bereich im Aufbau"-Seite — Leere als Einladung zum Handeln. */
@Component({
  selector: 'app-platzhalter',
  imports: [],
  templateUrl: './platzhalter.html',
  styleUrl: './platzhalter.scss',
})
export class Platzhalter {
  private readonly route = inject(ActivatedRoute);

  protected readonly data = toSignal(
    this.route.data.pipe(
      map((d) => (d['platzhalter'] as PlatzhalterData) ?? { titel: 'Bereich', text: '' }),
    ),
    { initialValue: { titel: 'Bereich', text: '' } as PlatzhalterData },
  );
}
