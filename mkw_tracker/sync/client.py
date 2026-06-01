"""SyncClient stub - server upload/fetch for PBs and friends' PBs."""
from typing import Optional


class SyncClient:
    """
    Stub for future server synchronisation.

    upload_pb: push a local PB run to the server.
    fetch_friends_pbs: pull friends' PBs from the server for a given course.
    """

    def __init__(self, server_url: str = "", auth_token: str = ""):
        self._server_url  = server_url
        self._auth_token  = auth_token

    def upload_pb(self, mkwreplay: dict) -> bool:
        """
        Upload a PB run to the server.
        Returns True on success, False on failure.
        Not yet implemented - always returns False.
        """
        print("[SyncClient] upload_pb: not yet implemented")
        return False

    def fetch_friends_pbs(self, course: str) -> list:
        """
        Fetch friends' PBs for *course* from the server.
        Returns a list of mkwreplay dicts.
        Not yet implemented - always returns [].
        """
        print("[SyncClient] fetch_friends_pbs: not yet implemented")
        return []
