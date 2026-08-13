# OAuth Login

Sign in to YouTube Music with Google's OAuth device flow instead of browser cookies.

## Why

The default `ytm setup` extracts your browser's YouTube Music cookies. That needs no setup at
all, but cookies are short-lived — Google rotates them, so most people end up running
`ytm setup` again every day or so to keep playback working. OAuth access tokens refresh
themselves automatically in the background, so once you're signed in you generally stay signed
in.

The tradeoff is a one-time setup step: OAuth needs its own Google Cloud OAuth client. ytm-player
does not ship a shared client ID, so you create your own, free, in about five minutes.

## One-time Google Cloud setup

1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and create a new project
   (or reuse an existing one).
2. Under **APIs & Services > Library**, search for **YouTube Data API v3** and enable it.
3. Under **APIs & Services > OAuth consent screen**, configure a consent screen (External is
   fine for personal use; you don't need to submit it for verification).
4. Under **APIs & Services > Credentials**, click **Create Credentials > OAuth client ID**.
5. For **Application type**, choose **TVs and Limited Input devices** — this is the client type
   that supports the device flow ytm-player uses (enter a code on another screen, rather than a
   browser redirect back to the app).
6. Copy the **Client ID** and **Client Secret** shown after creation. You'll paste these into
   ytm-player once; they're saved locally for reuse.

## Signing in

### From the TUI

Open the command palette (`ctrl+p`) and run **Account: Sign in with Google (OAuth)**. Paste your
Client ID and Client Secret when prompted (only needed the first time), then follow the on-screen
link and code. ytm-player copies the sign-in link to your clipboard automatically where possible.

A successful sign-in takes effect on your *next* launch of `ytm` — restart it once to pick up the
new session.

### From the CLI

```bash
ytm setup --oauth
```

Prompts for your Client ID and Client Secret the first time (or pass `--client-id`/
`--client-secret` directly), then walks you through the same device-flow login: open the printed
URL, enter the code, and the command waits until you finish.

Once configured, `ytm setup --oauth` reuses your saved client credentials automatically — you
only need to paste them again if you ever revoke them in Google Cloud Console.

## Where things are stored

- `~/.config/ytm-player/oauth.json` — the OAuth token (access + refresh token). Refreshed
  automatically; never needs manual editing.
- `~/.config/ytm-player/oauth_creds.json` — your Google OAuth Client ID/Secret, saved after the
  first run so you don't re-enter them.

Both files are written with owner-only permissions (`0600`).

## Switching back to cookies

Delete `oauth.json` and `oauth_creds.json`, then run `ytm setup` (without `--oauth`) again.
ytm-player prefers OAuth automatically whenever both OAuth files are present, so removing them
is enough to fall back to cookie-based auth.
