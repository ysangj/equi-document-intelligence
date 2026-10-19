/**
 * Dev-only Google OAuth + Drive state for the mock api (FR-2/FR-3, T7 contract):
 * the connection, the watched folder, a small folder tree for the picker and the
 * simulated consent round trip. Errors follow C2/C7 (409 `demo_mode`,
 * `not_connected`, 404 `not_found`, 422 `validation_error`).
 */
import { AuthStatusResponse, DriveFolder, DriveFoldersResponse, FolderRef, OAuthErrorCode, WatchResponse } from '../api/models';
import { MockResponse, err } from './mock-http';

export const MOCK_EMAIL = 'sj.demo@gmail.com';
export const DRIVE_SCOPE = 'https://www.googleapis.com/auth/drive.readonly';
export const FUND_DOCS_ID = '1Q8vXk2mFundDocs';
const PAGE_SIZE = 100;

interface Node {
  id: string;
  name: string;
  parent: string | null;
  modifiedOffsetDays: number;
}

/** My Drive. `Monthly exports` has 130 subfolders so the picker pages ("Load more"). */
function buildTree(): Map<string, Node> {
  const nodes: Node[] = [
    { id: 'root', name: 'My Drive', parent: null, modifiedOffsetDays: 0 },
    { id: FUND_DOCS_ID, name: 'Fund Docs', parent: 'root', modifiedOffsetDays: 1 },
    { id: '1Zp3TaxArchive', name: 'Tax archive', parent: 'root', modifiedOffsetDays: 197 },
    { id: '1Bp9BoardPacks', name: 'board packs', parent: 'root', modifiedOffsetDays: 40 },
    { id: '1Me7MonthlyExports', name: 'Monthly exports', parent: 'root', modifiedOffsetDays: 3 },
    { id: '1Pe4Personal', name: 'Personal', parent: 'root', modifiedOffsetDays: 12 },
    { id: '1Fd2025Archive', name: '2025 archive', parent: FUND_DOCS_ID, modifiedOffsetDays: 60 },
    { id: '1FdStatements', name: 'Statements', parent: FUND_DOCS_ID, modifiedOffsetDays: 5 },
    { id: '1Tx2024', name: '2024', parent: '1Zp3TaxArchive', modifiedOffsetDays: 400 },
    { id: '1Tx2025', name: '2025', parent: '1Zp3TaxArchive', modifiedOffsetDays: 197 },
  ];
  for (let k = 0; k < 130; k++) {
    const y = 2015 + Math.floor(k / 12);
    const m = String((k % 12) + 1).padStart(2, '0');
    nodes.push({ id: `1Me7Month${k}`, name: `${y}-${m}`, parent: '1Me7MonthlyExports', modifiedOffsetDays: 130 - k });
  }
  return new Map(nodes.map((n) => [n.id, n]));
}

export interface MockDriveOptions {
  demoMode: boolean;
  connected: boolean;
  /** `?error=` code the simulated callback returns instead of success */
  oauthError: string | null;
  iso: (offsetSeconds: number) => string;
}

export class MockDrive {
  private readonly tree = buildTree();
  connected: boolean;
  email: string | null;
  connectedAt: string | null;
  folder: FolderRef | null;

  constructor(private readonly opts: MockDriveOptions) {
    this.connected = !opts.demoMode && opts.connected;
    this.email = this.connected ? MOCK_EMAIL : null;
    this.connectedAt = this.connected ? opts.iso(-3600) : null;
    this.folder = this.connected ? { source: 'drive', folder_id: FUND_DOCS_ID, folder_name: 'Fund Docs' } : null;
  }

  /** Drive-mode guard for drive/sync endpoints (C2: 409 not_connected / no_folder). */
  syncBlocked(): MockResponse | null {
    if (this.opts.demoMode) return null;
    if (!this.connected) return err(409, 'not_connected', 'Google Drive is not connected. Connect it first.');
    if (!this.folder) return err(409, 'no_folder', 'No Drive folder is being watched. Choose a folder first.');
    return null;
  }

  status(): AuthStatusResponse {
    if (this.opts.demoMode) {
      return { mode: 'demo', connected: false, google_email: null, scopes: [], connected_at: null, folder: { source: 'local', folder_id: null, folder_name: 'sample_docs' } };
    }
    return {
      mode: 'drive',
      connected: this.connected,
      google_email: this.email,
      // sorted, as the api grants them (T7)
      scopes: this.connected ? [DRIVE_SCOPE, 'https://www.googleapis.com/auth/userinfo.email', 'openid'] : [],
      connected_at: this.connectedAt,
      folder: this.connected ? this.folder : null,
    };
  }

  /** The stand-in for login → Google consent → callback: where the callback would redirect. */
  oauthCallback(): { location: string } {
    if (this.opts.demoMode) return { location: '/connect?error=demo_mode' };
    const code = this.opts.oauthError as OAuthErrorCode | null;
    if (code) return { location: `/connect?error=${encodeURIComponent(code)}` };
    this.connected = true;
    this.email = MOCK_EMAIL;
    this.connectedAt = this.opts.iso(0);
    return { location: '/connect/folder' };
  }

  disconnect(): MockResponse {
    if (this.opts.demoMode) return err(409, 'demo_mode', 'Google Drive is not used in demo mode.');
    this.connected = false;
    this.email = null;
    this.connectedAt = null;
    this.folder = null;
    return { status: 200, body: { connected: false } };
  }

  folders(params: URLSearchParams): MockResponse {
    for (const k of params.keys()) {
      if (k !== 'parent' && k !== 'page_token') return err(422, 'validation_error', 'Invalid request parameters.', [{ loc: `query.${k}`, message: 'Extra inputs are not permitted' }]);
    }
    if (this.opts.demoMode) return err(409, 'demo_mode', 'Google Drive is not used in demo mode.');
    if (!this.connected) return err(409, 'not_connected', 'Google Drive is not connected. Connect it first.');
    const parentId = params.get('parent') || 'root';
    const parent = this.tree.get(parentId);
    if (!parent) return err(404, 'not_found', `Drive folder ${parentId} not found.`);
    const children = [...this.tree.values()]
      .filter((n) => n.parent === parentId)
      .sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
    const offset = Number(params.get('page_token') ?? '0') || 0;
    const page = children.slice(offset, offset + PAGE_SIZE);
    const items: DriveFolder[] = page.map((n) => ({ folder_id: n.id, folder_name: n.name, modified_at: this.opts.iso(-n.modifiedOffsetDays * 86400) }));
    const body: DriveFoldersResponse = {
      parent: { folder_id: parent.id, folder_name: parent.name, parent_id: parent.parent, is_root: parent.id === 'root' },
      items,
      next_page_token: offset + PAGE_SIZE < children.length ? String(offset + PAGE_SIZE) : null,
    };
    return { status: 200, body };
  }

  /** Validates and stores the folder. The caller requests the reconcile. */
  watch(body: unknown): MockResponse {
    const b = (body ?? {}) as Record<string, unknown>;
    const extra = Object.keys(b).find((k) => k !== 'folder_id');
    if (extra) return err(422, 'validation_error', 'Invalid request parameters.', [{ loc: `body.${extra}`, message: 'Extra inputs are not permitted' }]);
    if (typeof b['folder_id'] !== 'string' || !b['folder_id']) {
      return err(422, 'validation_error', 'Invalid request parameters.', [{ loc: 'body.folder_id', message: 'Field required' }]);
    }
    if (this.opts.demoMode) return err(409, 'demo_mode', 'Google Drive is not used in demo mode.');
    if (!this.connected) return err(409, 'not_connected', 'Google Drive is not connected. Connect it first.');
    const node = this.tree.get(b['folder_id']);
    if (!node) return err(404, 'not_found', `Drive folder ${b['folder_id']} not found.`);
    this.folder = { source: 'drive', folder_id: node.id, folder_name: node.name };
    const res: WatchResponse = { folder: this.folder, reconcile_requested_at: this.opts.iso(0) };
    return { status: 200, body: res };
  }
}
