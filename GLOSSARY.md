# ytm-player

A terminal YouTube Music client. These are the names for what happens between "the user asked for music" and "music is playing".

## Playback ownership

**Playback request**:
A user's or a command's ask for something to play: a track, an album, a playlist, a radio. A newer request supersedes an older one. A request can end without ever starting playback.
_Avoid_: play call, job

**Play attempt**:
One committed try at making a specific track audible, made on behalf of a playback request. A request makes at most one attempt at a time; a failed attempt may be followed by a single retry attempt.
_Avoid_: play generation, load

**Background tail**:
Work that keeps filling the current queue after its request has finished: the rest of a long playlist, an artist's remaining songs, autoplay radio refill. It belongs to the queue's lifetime, not to any one request, so an ordinary Next does not cancel it.
_Avoid_: continuation, refill job

**Queue lifetime**:
The span between two queue replacements or clears. Work tied to an earlier queue lifetime must not touch the current queue.
_Avoid_: queue generation, queue version

**Stop**:
The user's explicit end of playback. It supersedes every playback request, play attempt and background tail at once.
_Avoid_: cancel, halt
