import { OAuthErrorCode } from '../../api/models';

/** Friendly copy for each `/connect?error=<code>` set by the OAuth callback (T3 decision 6). */
export const CONNECT_ERRORS: Record<OAuthErrorCode, string> = {
  access_denied: 'Google sign-in was cancelled, so Drive was not connected. Connect again when you are ready.',
  state_mismatch: 'The sign-in session expired or came from another tab. Please start again from this page.',
  token_exchange_failed: 'Google did not accept the sign-in. Please try connecting again.',
  missing_refresh_token:
    'Google did not grant offline access. Remove this app under your Google account’s third-party access, then connect again.',
  insufficient_scope: 'Read-only access to Google Drive was not granted. Connect again and allow Drive access on the consent screen.',
  demo_mode: 'The app is running in demo mode, so Google Drive is not used.',
};

/** Message for any `?error=` value; unknown codes get a generic line that names the code. */
export function connectErrorMessage(code: string | null | undefined): string | null {
  if (!code) return null;
  return (CONNECT_ERRORS as Record<string, string>)[code] ?? `Connecting Google Drive failed (${code}). Please try again.`;
}
