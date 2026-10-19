import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { ApiError } from '../../api/api-error';
import { TableStateComponent } from './table-state.component';

describe('TableStateComponent: Drive-state errors link to the connect screens', () => {
  async function render(error: ApiError) {
    TestBed.configureTestingModule({ providers: [provideRouter([])] });
    const fixture = TestBed.createComponent(TableStateComponent);
    fixture.componentRef.setInput('noun', 'documents');
    fixture.componentRef.setInput('error', error);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }
  const e = (code: ApiError['code']): ApiError => ({ status: 409, code, message: 'msg', details: null, envelope: true });

  it('409 not_connected → Connect Google Drive (/connect)', async () => {
    const link = (await render(e('not_connected'))).querySelector('[data-testid=table-connect]')!;
    expect(link.getAttribute('href')).toBe('/connect');
    expect(link.textContent).toContain('Connect Google Drive');
  });

  it('409 no_folder → Choose a folder (/connect/folder)', async () => {
    expect((await render(e('no_folder'))).querySelector('[data-testid=table-connect]')!.getAttribute('href')).toBe('/connect/folder');
  });

  it('other errors get no connect link', async () => {
    expect((await render(e('db_unavailable'))).querySelector('[data-testid=table-connect]')).toBeNull();
  });
});
