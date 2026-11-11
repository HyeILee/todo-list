# Owner-only Google login

Google is another first factor for the **existing owner account**, followed by the
same conditional passkey/recovery-code check as password login. There is no
Google sign-up route, email-based auto-linking, or copying/moving of diary data.

## Activation

1. In the owner's Google Cloud project, configure Google Auth Platform for
   PERSONAL DAY LOG and create an OAuth client of type **Web application**.
   For personal testing, explicitly list the owner's Google account as a test
   user. Request only `openid` and `email`; no Gmail, Drive or Calendar APIs.
2. Register this exact authorized redirect URI:
   `https://todo-day-log.vercel.app/accounts/google/callback/`
   This server redirect flow does not require a JavaScript origin.
3. Save the client ID and client secret only in the existing Vercel project's
   **Production** environment as `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
   Set `GOOGLE_LOGIN_ENABLED=True`. Do not paste secrets into chat, git, logs,
   previews, the browser bundle, or a document. Use separate clients for tests.
4. Redeploy, then sign in with the existing owner password. Open
   `/accounts/security/`, register a passkey and save recovery codes if necessary,
   and turn on additional authentication. Verify with that passkey, enter the
   current site password in the Google connection card, and choose the owner's
   Google account. Linking expires after five minutes.
5. Sign out and test Google login. An unremembered browser must stop at the
   passkey/recovery-code screen before it can access any records. Remembering a
   browser skips that screen for 30 days only with the same secret cookie, IP,
   browser, security generation and password hash. Test a second Google account:
   it must be refused and must not create an application user.

Keep the existing password/passkey recovery route. While Google is linked,
turning off additional authentication is refused. To change the Google account,
first unlink it after fresh password and passkey verification, then explicitly
link the new account. Unlinking invalidates pending Google logins and active
Google sessions on other devices while keeping saved recovery codes usable.

## Security and deployment

- Authlib verifies state, PKCE S256, nonce, RS256 signature, issuer, client
  audience and expiry. Identity is keyed by Google's immutable `sub`.
- A database-backed intent is single-use, bound to the initiating server-side
  session and expires after five minutes. Link intents additionally bind the
  existing user, password hash and passkey security generation. Start/link/unlink
  require POST and CSRF. Provider errors are never echoed or logged with tokens.
- Only the verified Google subject and email are saved. Provider access, refresh
  and ID tokens are never written to the database or application session.
- OAuth callbacks use the existing canonical `PASSKEY_ORIGIN`. The old alias is
  still usable for password login, but linking/Google initiation must use the
  canonical address. Production credentials must never reach test previews.
- Migration `0010` adds two independent tables. No existing records or users are
  rewritten. With missing credentials or the flag off, the existing login works;
  the Google button is hidden and the owner settings show connection preparation.
- To roll back activation, set `GOOGLE_LOGIN_ENABLED=False` and redeploy. Do not
  delete existing owner accounts, passkeys or records.

Official references:
- https://developers.google.com/identity/openid-connect/openid-connect
- https://docs.authlib.org/en/stable/oauth2/client/web/django.html
