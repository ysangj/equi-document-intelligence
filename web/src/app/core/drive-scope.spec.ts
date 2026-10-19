import { AUTH_CONNECTED } from '../testing/fixtures';
import { DRIVE_READONLY_SCOPE, driveAccess } from './drive-scope';

describe('driveAccess (FR-2 scope label)', () => {
  it('the three scopes the api grants read as "Read-only access to Google Drive" (identity scopes ignored)', () => {
    const a = driveAccess(AUTH_CONNECTED.scopes);
    expect(AUTH_CONNECTED.scopes.length).toBe(3);
    expect(a).toEqual({
      label: 'Read-only access to Google Drive',
      readOnly: true,
      raw: 'https://www.googleapis.com/auth/drive.readonly\nhttps://www.googleapis.com/auth/userinfo.email\nopenid',
    });
  });

  it('drive.readonly alone is read-only too', () => {
    expect(driveAccess([DRIVE_READONLY_SCOPE]).label).toBe('Read-only access to Google Drive');
  });

  it('a broader Drive scope is named, not called read-only', () => {
    const a = driveAccess([DRIVE_READONLY_SCOPE, 'https://www.googleapis.com/auth/drive']);
    expect(a.readOnly).toBe(false);
    expect(a.label).toBe('Google Drive access: drive.readonly, drive');
  });

  it('no Drive scope (identity only, empty or null) says so', () => {
    expect(driveAccess(['openid', 'https://www.googleapis.com/auth/userinfo.email']).label).toBe('No Google Drive access granted');
    expect(driveAccess([]).label).toBe('No Google Drive access granted');
    expect(driveAccess(null).raw).toBe('');
  });

  it('does not treat look-alike prefixes as Drive scopes', () => {
    expect(driveAccess(['https://www.googleapis.com/auth/drivelabels']).label).toBe('No Google Drive access granted');
  });
});
