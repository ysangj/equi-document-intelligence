/**
 * Human label for the OAuth scopes in `/api/auth/status.scopes` (FR-2).
 *
 * The api grants `drive.readonly` plus `openid` and `userinfo.email` (identity only,
 * used to read the account email). Users see the Drive access as a sentence; the
 * raw scope URLs go in a tooltip / details.
 */
export const DRIVE_READONLY_SCOPE = 'https://www.googleapis.com/auth/drive.readonly';

const DRIVE_PREFIX = 'https://www.googleapis.com/auth/drive';

export interface DriveAccess {
  /** e.g. "Read-only access to Google Drive" */
  label: string;
  /** true when drive.readonly is the only Drive scope granted */
  readOnly: boolean;
  /** the raw scopes, as granted, one per line (tooltip text) */
  raw: string;
}

/** Drive scopes are `.../auth/drive` and `.../auth/drive.<suffix>`; identity scopes are not. */
function isDriveScope(s: string): boolean {
  return s === DRIVE_PREFIX || s.startsWith(DRIVE_PREFIX + '.');
}

export function driveAccess(scopes: readonly string[] | null | undefined): DriveAccess {
  const list = [...(scopes ?? [])];
  const raw = list.join('\n');
  const drive = list.filter(isDriveScope);
  if (!drive.length) return { label: 'No Google Drive access granted', readOnly: false, raw };
  if (drive.every((s) => s === DRIVE_READONLY_SCOPE)) {
    return { label: 'Read-only access to Google Drive', readOnly: true, raw };
  }
  const names = drive.map((s) => s.slice('https://www.googleapis.com/auth/'.length));
  return { label: `Google Drive access: ${names.join(', ')}`, readOnly: false, raw };
}
