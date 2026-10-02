"""The datasets each person is currently looking at in the web app.

Everyone starts with the provided Starbucks files. An upload only replaces the data for the
browser session that uploaded it, so one person's file never changes what another person sees.
"""
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
        self._lock = threading.Lock()

    def get(self, sid: str) -> dict:
        """This session's datasets, starting from the defaults on first use."""
        with self._lock:
            if sid not in self._sessions:
                self._sessions[sid] = dict(self.defaults)
                if len(self._sessions) > self.max_sessions:
                    self._sessions.popitem(last=False)
            self._sessions.move_to_end(sid)
            return self._sessions[sid]

    def replace(self, sid: str, loaded: dict) -> None:
        """Swap in newly uploaded datasets for one session only."""
        data = self.get(sid)
        with self._lock:
            data.update(loaded)

    def reset(self, sid: str) -> None:
        """Return a session to the provided files."""
        self.replace(sid, self.defaults)

    def using_defaults(self, sid: str) -> bool:
        """True if the session is still on the provided files (no upload)."""
        data = self.get(sid)
        return all(data[k] is self.defaults[k] for k in self.defaults)
