"""
CO-37 - Checkmk REST-API Client (API 1.0)

API-Version 1.0, Basis-URL:
    https://<server>/<site>/check_mk/api/1.0

Authentifizierung ueber Automation-User:
    Authorization: Bearer <username> <secret>
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx


class CheckmkError(Exception):
    pass


class CheckmkClient:
    def __init__(
        self,
        server_url: str,
        site: str,
        username: str,
        secret: str,
        verify_ssl: bool = True,
        timeout: float = 20.0,
    ):
        # Benutzer und Secret landen unveraendert in einer HTTP-Kopfzeile.
        # httpx kodiert Kopfzeilen als ASCII und wirft sonst einen
        # UnicodeEncodeError - der kam bisher ungefangen aus der Route
        # heraus und wurde zu 500 (F-54 der Pruefung vom 2026-09-03, beim
        # Fuzzing gefunden). Ein Umlaut im Automation-Benutzer genuegte.
        #
        # Hier statt in der Route, weil auch der Hintergrundbetrieb ueber
        # get_checkmk() Clients baut. CheckmkError faengt die Route schon
        # ab und macht daraus eine lesbare 400.
        #
        # Der Test deckt nebenbei Zeilenumbrueche ab: '\r' und '\n' sind
        # in ASCII, wuerden also durchkommen. httpx weist sie selbst ab,
        # aber auch das erst mit einem Abbruch mitten in der Route.
        for name, wert in (("Benutzer", username), ("Secret", secret)):
            if not wert.isascii() or any(c in wert for c in "\r\n\0"):
                raise CheckmkError(
                    f"Der Checkmk-{name} enthaelt Zeichen, die in einer "
                    f"HTTP-Kopfzeile nicht zulaessig sind (nur ASCII, "
                    f"keine Zeilenumbrueche).")

        self.base_url = f"{server_url.rstrip('/')}/{site}/check_mk/api/1.0"
        self.site = site
        self._headers = {
            "Authorization": f"Bearer {username} {secret}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self._verify = verify_ssl
        self._timeout = timeout

    # ------------------------------------------------------------------
    # intern
    # ------------------------------------------------------------------
    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        hdrs = dict(self._headers)
        if headers:
            hdrs.update(headers)

        # Alles, was httpx an Netzfehlern wirft, wird hier zu CheckmkError
        # (F-55 der Pruefung vom 2026-09-03). Vorher kam ein nicht
        # erreichbarer Checkmk-Server als ConnectError/TimeoutException aus
        # der Route heraus und wurde zu 500 "Internal Server Error" - fuer
        # den haeufigsten Fall ueberhaupt, naemlich eine falsch getippte
        # Adresse beim Einrichten. Gefunden, als der Ausgangs-Proxy des
        # Testcontainers eine Verbindung abwies.
        #
        # Hier statt in den Routen: _request ist der einzige Ort, an dem
        # dieser Client ueberhaupt ins Netz geht, und jede der acht
        # Aufruferstellen faengt CheckmkError bereits ab.
        try:
            async with httpx.AsyncClient(verify=self._verify,
                                         timeout=self._timeout) as client:
                resp = await client.request(method, url, json=json,
                                            params=params, headers=hdrs)
        except httpx.HTTPError as exc:
            raise CheckmkError(
                f"Checkmk nicht erreichbar ({type(exc).__name__}): {exc}")

        if resp.status_code == 204:
            return None
        if resp.status_code >= 400:
            raise CheckmkError(
                f"Checkmk {resp.status_code} bei {method} {path}: {resp.text[:500]}"
            )
        if not resp.content:
            return None
        return resp.json()

    @staticmethod
    def _iso(dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()

    # ------------------------------------------------------------------
    # Verbindung
    # ------------------------------------------------------------------
    async def test_connection(self) -> dict:
        """Prueft Zugangsdaten und liefert Versionsinfo."""
        data = await self._request("GET", "/version")
        return {
            "ok": True,
            "versions": data.get("versions", {}),
            "edition": data.get("edition"),
            "site": data.get("site", self.site),
        }

    # ------------------------------------------------------------------
    # Hosts
    # ------------------------------------------------------------------
    async def list_hosts(self) -> list[dict]:
        """Alle in Checkmk konfigurierten Hosts (host_config)."""
        data = await self._request(
            "GET",
            "/domain-types/host_config/collections/all",
            params={"effective_attributes": "false"},
        )
        hosts = []
        for entry in data.get("value", []):
            ext = entry.get("extensions", {}) or {}
            attrs = ext.get("attributes", {}) or {}
            hosts.append(
                {
                    "name": entry.get("id") or ext.get("name"),
                    "folder": ext.get("folder"),
                    "address": attrs.get("ipaddress"),
                    "alias": attrs.get("alias"),
                    "is_cluster": ext.get("is_cluster", False),
                }
            )
        return hosts

    async def get_host_state(self, host_name: str) -> Optional[dict]:
        """Aktueller Monitoring-Zustand eines Hosts."""
        data = await self._request(
            "GET",
            "/domain-types/host/collections/all",
            params={
                "query": f'{{"op": "=", "left": "name", "right": "{host_name}"}}',
                "columns": ["name", "state", "last_check", "scheduled_downtime_depth"],
            },
        )
        values = data.get("value", [])
        if not values:
            return None
        ext = values[0].get("extensions", {})
        return {
            "name": ext.get("name"),
            "state": ext.get("state"),  # 0 UP, 1 DOWN, 2 UNREACH
            "last_check": ext.get("last_check"),
            "in_downtime": bool(ext.get("scheduled_downtime_depth", 0)),
        }

    # ------------------------------------------------------------------
    # Downtimes
    # ------------------------------------------------------------------
    async def set_host_downtime(
        self,
        host_name: str,
        minutes: int = 30,
        comment: str = "CO-37: Patch-Vorgang",
        include_services: bool = True,
    ) -> dict:
        """
        Setzt eine feste Downtime auf Host (und optional alle Services).
        Gibt Startzeit zurueck - Checkmk liefert bei Anlage keine ID.
        """
        start = datetime.now(timezone.utc)
        end = start + timedelta(minutes=minutes)

        body = {
            "start_time": self._iso(start),
            "end_time": self._iso(end),
            "recur": "fixed",
            "duration": 0,
            "comment": comment,
            "downtime_type": "host",
            "host_name": host_name,
        }
        await self._request(
            "POST", "/domain-types/downtime/collections/host", json=body
        )

        if include_services:
            svc_body = {
                "start_time": self._iso(start),
                "end_time": self._iso(end),
                "recur": "fixed",
                "duration": 0,
                "comment": comment,
                "downtime_type": "host_by_query",
                "query": {"op": "=", "left": "host_name", "right": host_name},
            }
            try:
                await self._request(
                    "POST",
                    "/domain-types/downtime/collections/service",
                    json=svc_body,
                )
            except CheckmkError:
                # Services optional - Host-Downtime deckt den Kernfall ab
                pass

        return {
            "host_name": host_name,
            "start": self._iso(start),
            "end": self._iso(end),
            "comment": comment,
        }

    async def list_downtimes(self, host_name: Optional[str] = None) -> list[dict]:
        params = {}
        if host_name:
            params["host_name"] = host_name
        data = await self._request(
            "GET", "/domain-types/downtime/collections/all", params=params
        )
        out = []
        for entry in data.get("value", []):
            ext = entry.get("extensions", {})
            out.append(
                {
                    "id": entry.get("id"),
                    "host_name": ext.get("host_name"),
                    "service_description": ext.get("service_description"),
                    "comment": ext.get("comment"),
                    "start_time": ext.get("start_time"),
                    "end_time": ext.get("end_time"),
                    "is_service": ext.get("is_service"),
                }
            )
        return out

    async def set_downtime_multi(
        self,
        host_names: list[str],
        minutes: int = 30,
        comment: str = "CO-37",
        include_services: bool = True,
    ) -> dict:
        """
        Setzt dieselbe Downtime auf mehrere Hosts.
        Liefert Erfolge und Fehler getrennt zurueck, damit der Aufrufer
        entscheiden kann, ob ein Neustart trotzdem erlaubt ist.
        """
        ok, failed = [], {}
        for name in host_names:
            try:
                await self.set_host_downtime(
                    name, minutes=minutes, comment=comment,
                    include_services=include_services,
                )
                ok.append(name)
            except CheckmkError as exc:
                failed[name] = str(exc)
        return {"ok": ok, "failed": failed, "minutes": minutes}

    async def remove_downtime_multi(
        self, host_names: list[str], comment_filter: str = "CO-37"
    ) -> dict:
        removed, failed = {}, {}
        for name in host_names:
            try:
                removed[name] = await self.remove_downtime(name, comment_filter)
            except CheckmkError as exc:
                failed[name] = str(exc)
        return {"removed": removed, "failed": failed}

    async def remove_downtime(
        self, host_name: str, comment_filter: str = "CO-37"
    ) -> int:
        """
        Entfernt Downtimes eines Hosts. Filtert optional nach Kommentar,
        damit fremde Wartungsfenster nicht geloescht werden.
        """
        downtimes = await self.list_downtimes(host_name=host_name)
        targets = [
            d for d in downtimes if comment_filter.lower() in (d["comment"] or "").lower()
        ]
        if not targets:
            return 0

        body = {
            "delete_type": "by_id",
            "downtime_id": None,
        }
        removed = 0
        for dt in targets:
            body["downtime_id"] = str(dt["id"])
            try:
                await self._request(
                    "POST", "/domain-types/downtime/actions/delete/invoke", json=body
                )
                removed += 1
            except CheckmkError:
                continue
        return removed
