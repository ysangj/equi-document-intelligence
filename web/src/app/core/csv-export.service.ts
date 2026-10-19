import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';

import { toApiError } from '../api/api-error';
import { isMockEnabled } from './mock-flag';
import { NotifyService } from './notify.service';

/** Something that can navigate the window (injected for tests). */
export interface Navigator {
  assign(url: string): void;
}

/**
 * C5: CSV export is a plain navigation to the list URL plus `format=csv`; the browser
 * downloads the attachment. With the mock api on, navigation would bypass the HTTP
 * interceptor, so the CSV is fetched through HttpClient and saved as a blob instead.
 */
@Injectable({ providedIn: 'root' })
export class CsvExportService {
  private readonly http = inject(HttpClient);
  private readonly notify = inject(NotifyService);

  /** The last URL exported (handy for tests and debugging). */
  lastUrl: string | null = null;

  navigator: Navigator = {
    assign: (url: string) => window.location.assign(url),
  };

  download(url: string): void {
    this.lastUrl = url;
    if (!isMockEnabled()) {
      this.navigator.assign(url);
      return;
    }
    this.http.get(url, { responseType: 'text', observe: 'response' }).subscribe({
      next: (res) => {
        const cd = res.headers.get('Content-Disposition') ?? '';
        const name = /filename="([^"]+)"/.exec(cd)?.[1] ?? 'export.csv';
        const blob = new Blob([res.body ?? ''], { type: 'text/csv;charset=utf-8' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = name;
        a.click();
        setTimeout(() => URL.revokeObjectURL(a.href), 1000);
      },
      error: (e) => this.notify.error(toApiError(e), 'CSV export failed'),
    });
  }
}
