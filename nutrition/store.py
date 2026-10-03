import secrets
import threading
from collections import OrderedDict


class DataStore:
    """Each browser session's datasets, kept in memory and capped at max_sessions."""

    def __init__(self, defaults: dict, max_sessions: int = 200):
        self.defaults = defaults  # {'drinks': (df, report), 'food': (df, report)}
        self.max_sessions = max_sessions
        # Sessions are kept in last-used order; the oldest is dropped once max_sessions is
        # reached, so memory stays bounded without a database.
        self._sessions: OrderedDict = OrderedDict()
        self._versions: dict = {}  # sid -> a token that changes whenever that session's data changes
        self._lock = threading.Lock()

    def get(self, sid: str) -> dict:
        """This session's datasets, starting from the defaults on first use."""
        with self._lock:
            if sid not in self._sessions:
                self._sessions[sid] = dict(self.defaults)
                if len(self._sessions) > self.max_sessions:
                    old, _ = self._sessions.popitem(last=False)
                    self._versions.pop(old, None)
            self._sessions.move_to_end(sid)
            return self._sessions[sid]

    def version(self, sid: str) -> str:
        """Which data a session has: 'sample' for the provided files, otherwise a random token per change.

        The page compares it with the version it last rendered. Data kept only in memory is lost when the
        server restarts, and a fresh server answers 'sample', so a page still showing an upload notices.
        """
        return self._versions.get(sid, "sample")

    def _changed(self, sid: str, data: dict) -> None:
        self._versions[sid] = "sample" if self._is_default(data) else secrets.token_hex(6)

    def _is_default(self, data: dict) -> bool:
        return set(data) == set(self.defaults) and all(data[k] is self.defaults[k] for k in self.defaults)

    def replace(self, sid: str, loaded: dict) -> None:
        """Swap in newly uploaded datasets for one session only.

        The first upload leaves the provided files behind, so uploading just drinks gives a drinks-only
        session instead of your drinks next to someone else's food. Later uploads add to what you have.
        """
        data = self.get(sid)
        with self._lock:
            if self._is_default(data):
                data.clear()
            data.update(loaded)
            self._changed(sid, data)

    def remove(self, sid: str, kind: str) -> bool:
        """Drop one uploaded dataset, keeping the other. Removing the last one returns to the provided files.
        Returns False when there is nothing uploaded to remove."""
        data = self.get(sid)
        with self._lock:
            if self._is_default(data) or kind not in data:
                return False
            del data[kind]
            if not data:
                data.update(self.defaults)
            self._changed(sid, data)
            return True

    def reset(self, sid: str) -> None:
        """Return a session to the provided files."""
        data = self.get(sid)
        with self._lock:
            data.clear()
            data.update(self.defaults)
            self._changed(sid, data)

    def using_defaults(self, sid: str) -> bool:
        """True if the session is still on the provided files (no upload)."""
        return self._is_default(self.get(sid))
