"""Reporte de lesiones NBA (feed público de ESPN, gratis) para detectar noticias."""
from __future__ import annotations

from typing import Iterable

import requests

from .storage import InjuryKey

ESPN_INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
TIMEOUT_S = 15
# ESPN y The Odds API nombran distinto a algunos equipos
TEAM_ALIASES = {"LA Clippers": "Los Angeles Clippers"}


def normalize_team(name: str) -> str:
    return TEAM_ALIASES.get(name, name)


def fetch_injuries() -> set[InjuryKey]:
    resp = requests.get(ESPN_INJURIES_URL, timeout=TIMEOUT_S)
    resp.raise_for_status()
    report: set[InjuryKey] = set()
    for team in resp.json().get("injuries", []):
        team_name = normalize_team(team.get("displayName", ""))
        for item in team.get("injuries", []):
            player = item.get("athlete", {}).get("displayName")
            status = item.get("status")
            if player and status:
                report.add((team_name, player, status))
    return report


def news_for_teams(previous: set[InjuryKey] | None, current: set[InjuryKey] | None,
                   teams: Iterable[str]) -> list[str]:
    """Altas/cambios de estatus desde la corrida anterior para los equipos dados."""
    if previous is None or current is None:
        return []
    wanted = set(teams)
    changes = sorted(key for key in current - previous if key[0] in wanted)
    return [f"{player} ({team}): {status}" for team, player, status in changes]
