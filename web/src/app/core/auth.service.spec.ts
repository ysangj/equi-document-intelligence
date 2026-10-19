import { HttpErrorResponse, provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { MatSnackBar } from '@angular/material/snack-bar';
import { Router, provideRouter } from '@angular/router';
import { Subject } from 'rxjs';

import { connectActionLabel, connectRouteFor, toApiError } from '../api/api-error';
import { NO_MATERIAL_ANIMATIONS, flushAsync } from '../testing/dialog';
import { AUTH_CONNECTED } from '../testing/fixtures';
import { AuthService } from './auth.service';
import { BrowserLocation } from './browser-location';
import { NotifyService } from './notify.service';
import { ProgressService } from './progress.service';

describe('AuthService', () => {
  let http: HttpTestingController;
  let auth: AuthService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([]), NO_MATERIAL_ANIMATIONS, { provide: BrowserLocation, useValue: { assign: vi.fn() } }],
    });
    http = TestBed.inject(HttpTestingController);
    auth = TestBed.inject(AuthService);
    vi.spyOn(TestBed.inject(NotifyService), 'info').mockImplementation(() => undefined);
  });

  it('watch() posts the folder id, updates the status folder and refreshes progress', async () => {
    auth.status.set({ ...AUTH_CONNECTED, folder: null });
    const refresh = vi.spyOn(TestBed.inject(ProgressService), 'refresh');
    let done = false;
    auth.watch('1Zp3TaxArchive').subscribe(() => (done = true));
    const req = http.expectOne('/api/drive/watch');
    expect(req.request.body).toEqual({ folder_id: '1Zp3TaxArchive' });
    req.flush({ folder: { source: 'drive', folder_id: '1Zp3TaxArchive', folder_name: 'Tax archive' }, reconcile_requested_at: '2026-10-16T18:41:15Z' });
    expect(done).toBe(true);
    expect(auth.folder()!.folder_name).toBe('Tax archive');
    expect(refresh).toHaveBeenCalled();
  });

  it('disconnect() failure keeps the state and reports the C2 message', async () => {
    auth.status.set(AUTH_CONNECTED);
    const error = vi.spyOn(TestBed.inject(NotifyService), 'error').mockImplementation(() => undefined);
    const nav = vi.spyOn(TestBed.inject(Router), 'navigateByUrl');
    const p = auth.disconnect();
    http.expectOne('/api/auth/disconnect').flush({ error: { code: 'demo_mode', message: 'Not in demo mode.', details: null } }, { status: 409, statusText: 'Conflict' });
    expect(await p).toBe(false);
    expect(auth.connected()).toBe(true);
    expect(error).toHaveBeenCalled();
    expect(nav).not.toHaveBeenCalled();
  });

  it('login() navigates the whole page to the login URL', () => {
    auth.login();
    expect(TestBed.inject(BrowserLocation).assign).toHaveBeenCalledWith('/api/auth/google/login');
  });
});

describe('409 not_connected / no_folder → connect links', () => {
  const env = (code: string) => new HttpErrorResponse({ status: 409, error: { error: { code, message: 'm', details: null } } });

  it('maps codes to routes and labels', () => {
    expect(connectRouteFor(toApiError(env('not_connected')))).toBe('/connect');
    expect(connectRouteFor(toApiError(env('no_folder')))).toBe('/connect/folder');
    expect(connectRouteFor(toApiError(env('document_busy')))).toBeNull();
    expect(connectActionLabel('/connect')).toBe('Connect Google Drive');
    expect(connectActionLabel('/connect/folder')).toBe('Choose a folder');
  });

  it('the snackbar for a sync 409 offers the action and navigates', async () => {
    TestBed.configureTestingModule({ providers: [provideRouter([])] });
    const action = new Subject<void>();
    const open = vi.spyOn(TestBed.inject(MatSnackBar), 'open').mockReturnValue({ onAction: () => action.asObservable() } as never);
    const nav = vi.spyOn(TestBed.inject(Router), 'navigateByUrl').mockResolvedValue(true);
    TestBed.inject(NotifyService).error(toApiError(env('no_folder')), 'Sync failed');
    expect(open).toHaveBeenCalledWith('Sync failed: m', 'Choose a folder', expect.anything());
    action.next();
    await flushAsync(1);
    expect(nav).toHaveBeenCalledWith('/connect/folder');
  });

  it('toApiError maps the client timeout', async () => {
    const { TimeoutError } = await import('rxjs');
    expect(toApiError(new TimeoutError()).code).toBe('timeout');
  });
});
