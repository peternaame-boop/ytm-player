"""Account-probe limits for `ytm setup --slots`.

Kept free of third-party imports: cli.py needs these to declare the option,
and `ytm report` has to start on an install where requests or ytmusicapi is
missing. services/auth.py re-exports them.
"""

from __future__ import annotations

# Default number of Google multi-login account slots (x-goog-authuser 0..N-1)
# to probe during interactive setup and silent renewal. Google multi-login
# supports at least 10 accounts in browser sessions.
DEFAULT_ACCOUNT_PROBE_SLOTS = 10
MAX_ACCOUNT_PROBE_SLOTS = 30
