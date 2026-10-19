"""Genera data/dashboard.html: líneas actuales, movimiento, splits y alertas."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Sequence

from .action_network import Split
from .alerts import KIND_NAMES, MARKET_NAMES, SEVERITY_LABEL
from .config import Settings
from .lines import LinePoint, format_value
from .odds_client import Quote
from .public_betting import make_key
from .storage import Store

ALERT_WINDOW = timedelta(hours=24)
DIVERGENCE_PP = 10        # dinero vs tickets: diferencia que se resalta
SPARK_W, SPARK_H, SPARK_PAD = 132, 34, 4
REFRESH_S = 300
MARKET_ORDER = ("spreads", "totals", "h2h")
SEVERITY_CLASS = {"high": "sev-high", "medium": "sev-medium", "low": "sev-low"}

Sides = dict[str, tuple[float | None, int]]


def _short(team: str) -> str:
    return team.split()[-1]


def _current_text(market: str, sides: Sides, home: str, away: str) -> str:
    if market == "spreads" and "home" in sides:
        point, price = sides["home"]
        return f"{_short(home)} {point:+g} ({price:+d})"
    if market == "totals" and "over" in sides:
        point, price = sides["over"]
        return f"O/U {point:g} (O {price:+d})"
    if market == "h2h" and "home" in sides and "away" in sides:
        return f"{_short(home)} {sides['home'][1]:+d} · {_short(away)} {sides['away'][1]:+d}"
    return "—"


def _sparkline(points: Sequence[LinePoint], market: str) -> str:
    if not points:
        return ""
    values = [p.value for p in points]
    low, high = min(values), max(values)
    span = (high - low) or 1.0
    step = (SPARK_W - 2 * SPARK_PAD) / max(1, len(points) - 1)
    coords = [(SPARK_PAD + i * step,
               SPARK_H - SPARK_PAD - (v - low) / span * (SPARK_H - 2 * SPARK_PAD)) for i, v in enumerate(values)]
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    dots = "".join(
        f'<circle class="hit" cx="{x:.1f}" cy="{y:.1f}" r="6"><title>'
        f'{p.ts.astimezone().strftime("%d/%m %H:%M")}: {escape(format_value(market, p.value))} ({p.price:+d})'
        f'</title></circle>' for (x, y), p in zip(coords, points))
    last_x, last_y = coords[-1]
    return (f'<svg class="spark" viewBox="0 0 {SPARK_W} {SPARK_H}" width="{SPARK_W}" height="{SPARK_H}" '
            f'role="img" aria-label="Historia de la línea">'
            f'<polyline points="{path}" fill="none" stroke="var(--series-1)" stroke-width="2" '
            f'stroke-linejoin="round" stroke-linecap="round"/>'
            f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="4" fill="var(--series-1)" '
            f'stroke="var(--surface-1)" stroke-width="2"/>{dots}</svg>')


def _bar(label: str, first_pct: float | None, first: str, second: str) -> str:
    if first_pct is None:
        return f'<div class="bar-row"><span class="bar-label">{label}</span><span class="muted">—</span></div>'
    second_pct = 100 - first_pct
    first, second = escape(first), escape(second)
    return (f'<div class="bar-row"><span class="bar-label">{label}</span>'
            f'<div class="bar" role="img" aria-label="{label}: {first} {first_pct:.0f}%, {second} {second_pct:.0f}%">'
            f'<span class="seg seg-1" style="width:{first_pct:.0f}%" title="{first} {first_pct:.0f}%"></span>'
            f'<span class="seg seg-2" style="width:{second_pct:.0f}%" title="{second} {second_pct:.0f}%"></span>'
            f'</div><span class="bar-val">{first_pct:.0f} / {second_pct:.0f}</span></div>')


def _splits_cell(split: Split | None, market: str, home: str, away: str) -> str:
    first, second = ("Over", "Under") if market == "totals" else (_short(home), _short(away))
    if split is None:
        return '<span class="muted">Sin splits</span>'
    html = (_bar("Tickets", split.tickets_pct, first, second)
            + _bar("Dinero", split.money_pct, first, second))
    if split.money_pct is not None and abs(split.money_pct - split.tickets_pct) >= DIVERGENCE_PP:
        diff = split.money_pct - split.tickets_pct
        side = first if diff > 0 else second
        html += f'<div class="flag">Dinero +{abs(diff):.0f} pp sobre tickets en <b>{escape(side)}</b></div>'
    return html


def _movement(points: Sequence[LinePoint], market: str) -> str:
    if len(points) < 2:
        return '<span class="muted">sin cambios aún</span>'
    first, last = points[0], points[-1]
    delta = last.value - first.value
    unit = "pp" if market == "h2h" else ""
    return (f'{escape(format_value(market, first.value))} → {escape(format_value(market, last.value))} '
            f'<span class="delta">({delta:+.1f}{unit})</span>')


def _game_card(event_id: str, quotes: list[Quote], store: Store, splits: dict, alerts: list) -> str:
    q0 = quotes[0]
    home, away = q0.home, q0.away
    start = datetime.fromisoformat(q0.commence_time).astimezone().strftime("%a %d/%m %H:%M")
    by_market: dict[str, Sides] = defaultdict(dict)
    for q in quotes:
        by_market[q.market][q.outcome] = (q.point, q.price)
    rows = []
    for market in MARKET_ORDER:
        if market not in by_market:
            continue
        points = store.line_history(event_id, q0.book, market)
        split = splits.get(make_key(away, home, market))
        rows.append(f'<tr><th scope="row">{MARKET_NAMES[market]}</th>'
                    f'<td class="num">{escape(_current_text(market, by_market[market], home, away))}</td>'
                    f'<td>{_movement(points, market)}</td><td>{_sparkline(points, market)}</td>'
                    f'<td class="splits">{_splits_cell(split, market, home, away)}</td></tr>')
    chips = "".join(f'<span class="chip {SEVERITY_CLASS[sev]}">{escape(KIND_NAMES[kind])}</span>'
                    for kind, sev in sorted({(a[2], a[3]) for a in alerts if a[1] == event_id}))
    search = escape(f"{home} {away}".lower())
    return (f'<article class="card" data-search="{search}"><header class="card-head">'
            f'<h2>{escape(away)} <span class="muted">@</span> {escape(home)}</h2>'
            f'<span class="muted">{start} · {escape(q0.book)}</span>{chips}</header>'
            f'<div class="table-wrap"><table><thead><tr><th>Mercado</th><th>Actual</th><th>Apertura → actual</th>'
            f'<th>Historia</th><th>Splits (Action Network)</th></tr></thead><tbody>{"".join(rows)}</tbody>'
            f'</table></div></article>')


def _alerts_list(alerts: list) -> str:
    if not alerts:
        return '<p class="muted">Sin alertas en las últimas 24 h.</p>'
    items = "".join(
        f'<li><span class="chip {SEVERITY_CLASS[sev]}">{SEVERITY_LABEL[sev]}</span> '
        f'<span class="muted">{datetime.fromisoformat(ts).astimezone().strftime("%d/%m %H:%M")}</span> '
        f'{escape(msg.split("] ", 1)[-1])}</li>' for ts, _event, _kind, sev, msg in alerts)
    return f'<ul class="alerts">{items}</ul>'


def build_dashboard(store: Store, settings: Settings, now: datetime | None = None) -> Path:
    now = now or datetime.now(timezone.utc)
    run_ts = store.latest_quotes_run()
    quotes = store.quotes_for_run(run_ts) if run_ts else []
    games: dict[str, list[Quote]] = defaultdict(list)
    for q in quotes:
        if datetime.fromisoformat(q.commence_time) > now:
            games[q.event_id].append(q)
    splits = store.latest_splits()
    alerts = store.recent_alerts((now - ALERT_WINDOW).replace(microsecond=0).isoformat())
    remaining = store.last_remaining()
    updated = datetime.fromisoformat(run_ts).astimezone().strftime("%d/%m/%Y %H:%M") if run_ts else "—"
    high = sum(1 for a in alerts if a[3] == "high")
    cards = "".join(_game_card(eid, qs, store, splits, alerts) for eid, qs in games.items())
    html = TEMPLATE.format(
        refresh=REFRESH_S, updated=updated, games=len(games), high=high, total_alerts=len(alerts),
        remaining="—" if remaining is None else remaining, splits=len(splits),
        alerts=_alerts_list(alerts), cards=cards or '<p class="muted">Sin partidos próximos.</p>')
    settings.dashboard_html.parent.mkdir(parents=True, exist_ok=True)
    settings.dashboard_html.write_text(html, encoding="utf-8")
    return settings.dashboard_html


TEMPLATE = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{refresh}">
<title>NBA Odds Tracker</title>
<style>
:root {{ color-scheme: light; --page:#f3f3f1; --surface-1:#fcfcfb; --text-primary:#0b0b0b;
  --text-secondary:#52514e; --grid:#e1e0d9; --border:rgba(11,11,11,0.10);
  --series-1:#2a78d6; --series-2:#eb6834; --critical:#d03b3b; --serious:#ec835a; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ color-scheme: dark;
  --page:#111110; --surface-1:#1a1a19; --text-primary:#f0efec; --text-secondary:#c3c2b7;
  --grid:#2c2c2a; --border:rgba(255,255,255,0.10); --series-1:#3987e5; --series-2:#d95926; }} }}
:root[data-theme="dark"] {{ color-scheme: dark; --page:#111110; --surface-1:#1a1a19; --text-primary:#f0efec;
  --text-secondary:#c3c2b7; --grid:#2c2c2a; --border:rgba(255,255,255,0.10); --series-1:#3987e5; --series-2:#d95926; }}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:var(--page); color:var(--text-primary);
  font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif; }}
main {{ max-width:1180px; margin:0 auto; padding:24px 16px 48px; }}
h1 {{ font-size:22px; margin:0 0 4px; }} h2 {{ font-size:16px; margin:0; }}
.muted {{ color:var(--text-secondary); }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin:20px 0; }}
.tile, .card, .panel {{ background:var(--surface-1); border:1px solid var(--border); border-radius:10px; }}
.tile {{ padding:12px 14px; }} .tile b {{ display:block; font-size:24px; font-variant-numeric:tabular-nums; }}
.panel {{ padding:14px 16px; margin-bottom:20px; }}
.toolbar {{ display:flex; gap:12px; align-items:center; margin:8px 0 12px; flex-wrap:wrap; }}
input[type=search] {{ flex:1; min-width:200px; padding:8px 10px; border-radius:8px; border:1px solid var(--border);
  background:var(--surface-1); color:var(--text-primary); font:inherit; }}
.card {{ margin-bottom:14px; overflow:hidden; }}
.card-head {{ display:flex; gap:10px; align-items:baseline; flex-wrap:wrap; padding:12px 16px;
  border-bottom:1px solid var(--grid); }}
.table-wrap {{ overflow-x:auto; }}
table {{ width:100%; border-collapse:collapse; min-width:760px; }}
th, td {{ text-align:left; padding:8px 16px; border-bottom:1px solid var(--grid); vertical-align:middle; }}
thead th {{ font-size:12px; font-weight:600; color:var(--text-secondary); }}
tbody tr:last-child th, tbody tr:last-child td {{ border-bottom:0; }}
.num {{ font-variant-numeric:tabular-nums; white-space:nowrap; }}
.delta {{ color:var(--text-secondary); font-variant-numeric:tabular-nums; }}
.spark {{ display:block; overflow:visible; }} .spark .hit {{ fill:transparent; }}
.spark .hit:hover {{ fill:var(--series-1); fill-opacity:.25; }}
.splits {{ width:290px; }}
.bar-row {{ display:grid; grid-template-columns:52px 1fr 52px; gap:8px; align-items:center; margin:2px 0; }}
.bar-label, .bar-val {{ font-size:12px; color:var(--text-secondary); font-variant-numeric:tabular-nums; }}
.bar {{ display:flex; gap:2px; height:10px; }}
.seg {{ display:block; height:100%; }} .seg-1 {{ background:var(--series-1); border-radius:4px 0 0 4px; }}
.seg-2 {{ background:var(--series-2); border-radius:0 4px 4px 0; }}
.legend {{ display:flex; gap:14px; font-size:12px; color:var(--text-secondary); }}
.legend i {{ display:inline-block; width:10px; height:10px; border-radius:3px; margin-right:5px; vertical-align:-1px; }}
.flag {{ font-size:12px; margin-top:4px; }}
.chip {{ display:inline-block; font-size:11px; font-weight:600; padding:2px 8px; border-radius:999px;
  border:1px solid var(--border); color:var(--text-primary); }}
.chip::before {{ content:""; display:inline-block; width:7px; height:7px; border-radius:50%; margin-right:5px;
  background:var(--text-secondary); }}
.sev-high::before {{ background:var(--critical); }} .sev-medium::before {{ background:var(--serious); }}
.alerts {{ list-style:none; margin:0; padding:0; max-height:260px; overflow:auto; }}
.alerts li {{ padding:6px 0; border-bottom:1px solid var(--grid); }}
</style></head>
<body><main>
<h1>NBA Odds Tracker</h1>
<div class="muted">Último snapshot: {updated} · se recarga cada 5 min</div>
<section class="tiles">
  <div class="tile"><span class="muted">Partidos</span><b>{games}</b></div>
  <div class="tile"><span class="muted">Alertas altas (24 h)</span><b>{high}</b></div>
  <div class="tile"><span class="muted">Alertas totales (24 h)</span><b>{total_alerts}</b></div>
  <div class="tile"><span class="muted">Mercados con splits</span><b>{splits}</b></div>
  <div class="tile"><span class="muted">Créditos restantes</span><b>{remaining}</b></div>
</section>
<section class="panel"><h2>Alertas recientes</h2>{alerts}</section>
<div class="toolbar">
  <input type="search" id="q" placeholder="Filtrar por equipo…" aria-label="Filtrar por equipo">
  <div class="legend"><span><i style="background:var(--series-1)"></i>Local / Over</span>
  <span><i style="background:var(--series-2)"></i>Visitante / Under</span></div>
</div>
{cards}
</main>
<script>
document.getElementById('q').addEventListener('input', function (e) {{
  var term = e.target.value.trim().toLowerCase();
  document.querySelectorAll('.card').forEach(function (card) {{
    card.hidden = term && card.dataset.search.indexOf(term) === -1;
  }});
}});
</script>
</body></html>
"""
