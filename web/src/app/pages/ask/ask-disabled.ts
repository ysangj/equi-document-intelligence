import { ConfigService } from '../../core/config.service';
import { AskSessionService } from './ask-session.service';

/**
 * Why Ask can't be used: `features.ask` is off in `/api/config` (FR-14, no
 * ANTHROPIC_API_KEY), or the api answered 503 `ask_disabled`. Null when Ask works.
 * Kept apart from the panel component so the shell can use it without loading that chunk.
 */
export function askDisabledReason(config: ConfigService, session: AskSessionService): string | null {
  const f = config.feature('ask');
  if (!f.enabled) return f.reason ?? 'Ask is disabled on this api.';
  return session.serverDisabled();
}
