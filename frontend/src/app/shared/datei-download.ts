/**
 * Löst einen Datei-Download aus einem Blob aus: Object-URL + unsichtbarer
 * `<a download>`.
 *
 * Bewusst KEIN `window.open`/Direkt-Link auf die API-URL: die API ist
 * anmeldepflichtig, und ein neues Fenster trägt weder den CSRF-Header noch
 * verlässlich das Session-Cookie. Downloads laufen deshalb als Blob durch den
 * HttpClient (Auth-Interceptor) und werden hier lokal ausgelöst.
 *
 * Die Object-URL wird erst NACH dem aktuellen Task freigegeben — ein synchrones
 * `revokeObjectURL` bricht den Download in manchen Browsern ab.
 */
export function dateiDownloadAusloesen(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  } catch (e) {
    URL.revokeObjectURL(url);
    throw e;
  }
}
