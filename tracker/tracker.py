"""Orquesta una corrida: snapshot -> historia -> detección -> alertas."""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests

from .action_network import Split, dates_to_query, fetch_splits
from .alerts import Alert, dispatch
from .config import Settings
from .dashboard import build_dashboard
from .detectors import analyze
from .injuries import fetch_injuries, news_for_teams
from .odds_client import OddsApiError, Quote, Usage, fetch_events, fetch_odds
from .public_betting import PublicKey, load_public_pct, make_key
from .schedule import ET, estimate_paid_runs, is_dense, snapshot_due
from .storage import InjuryKey, Store

log = logging.getLogger(__name__)
DAYS_PER_MONTH = 30
ESTIMATE_DAYS = 7
SPLIT_DAYS = 4  # días de calendario (ET) a consultar en Action Network


def _in_active_hours(settings: Settings) -> bool:
    if settings.active_hours is None:
        return True
    start, end = settings.active_hours
    return start <= datetime.now().astimezone().hour < end


def _game_starts(settings: Settings, store: Store, now: datetime) -> tuple[list[datetime], Usage]:
    """Horarios de inicio: endpoint de eventos (gratis) + juegos de hoy ya vistos en la base,
    para conocer el primer juego del día aunque ya haya empezado. También devuelve el saldo actual."""
    events, usage = fetch_events(settings.api_key)
    starts = {datetime.fromisoformat(ev["commence_time"]) for ev in events}
    day_start = datetime.combine(now.astimezone(ET).date(), datetime.min.time(), ET)
    starts |= {datetime.fromisoformat(ts) for ts in store.commence_times_since(day_start)}
    return sorted(starts), usage


def _credits_left(fresh: Usage, store: Store) -> int | None:
    """Saldo actual de la key (headers del endpoint gratuito); si falta, el último guardado.
    Usar el saldo fresco evita quedar bloqueado tras cambiar de API key."""
    return fresh.remaining if fresh.remaining is not None else store.last_remaining()


def _safe_injuries() -> set[InjuryKey] | None:
    try:
        return fetch_injuries()
    except (requests.RequestException, ValueError) as exc:
        log.warning("No se pudo leer el reporte de lesiones de ESPN (%s); INFO usará solo el tamaño del salto",
                    type(exc).__name__)
        return None


def _safe_splits(now: datetime) -> list[Split]:
    try:
        return fetch_splits(dates_to_query(now, SPLIT_DAYS))
    except (requests.RequestException, ValueError) as exc:
        log.warning("No se pudo leer Action Network (%s); RLM usará solo public_betting.csv",
                    type(exc).__name__)
        return []


def _public_pct(splits: list[Split], settings: Settings) -> dict[PublicKey, float]:
    """% de tickets de Action Network; el CSV manual tiene prioridad si existe."""
    public = {make_key(s.away, s.home, s.market): s.tickets_pct for s in splits}
    return {**public, **load_public_pct(settings.public_betting_csv)}


def _detect(store: Store, run_ts: str, quotes: list[Quote], prev_injuries: set[InjuryKey] | None,
            curr_injuries: set[InjuryKey] | None, public: dict[PublicKey, float]) -> list[Alert]:
    lines = {(q.event_id, q.book, q.market): q for q in quotes}
    alerts: list[Alert] = []
    for (event_id, book, market), quote in sorted(lines.items()):
        series = store.line_history(event_id, book, market)
        if len(series) < 2:
            continue
        news = news_for_teams(prev_injuries, curr_injuries, (quote.home, quote.away))
        pct = public.get(make_key(quote.away, quote.home, market))
        for signal in analyze(series, market, news, pct):
            alerts.append(Alert(run_ts, event_id, f"{quote.away} @ {quote.home}",
                                datetime.fromisoformat(quote.commence_time), book, market,
                                series[-1].value, signal))
    return alerts


def _run(settings: Settings, store: Store, now: datetime, run_ts: str) -> None:
    if not _in_active_hours(settings):
        log.info("Fuera de ACTIVE_HOURS %s, no se consulta la API.", settings.active_hours)
        store.record_run(run_ts, "skipped_hours")
        return
    try:
        starts, fresh_usage = _game_starts(settings, store, now)
    except OddsApiError as exc:
        log.error("%s", exc)
        store.record_run(run_ts, "error")
        return
    horizon = now + timedelta(hours=settings.lookahead_hours)
    if not any(now < start <= horizon for start in starts):
        log.info("Sin partidos en las próximas %dh; se omite la llamada de pago.", settings.lookahead_hours)
        store.record_run(run_ts, "skipped_no_games")
        return
    dense = is_dense(starts, now)
    last_paid = store.last_paid_run()
    if not snapshot_due(last_paid, now, dense):
        log.info("Aún no toca snapshot (frecuencia %s, último de pago %s).",
                 "30 min" if dense else "60 min", last_paid.isoformat() if last_paid else "-")
        return
    remaining = _credits_left(fresh_usage, store)
    if remaining is not None and remaining < settings.min_credits:
        log.warning("Quedan %d créditos (< %d). Corrida omitida.", remaining, settings.min_credits)
        store.record_run(run_ts, "skipped_low_credits")
        return

    try:
        quotes, usage = fetch_odds(settings.api_key, settings.bookmakers, settings.markets, now)
    except OddsApiError as exc:
        log.error("%s", exc)
        store.record_run(run_ts, "error")
        return
    store.save_quotes(run_ts, quotes)

    curr_injuries = _safe_injuries()
    prev_injuries = store.previous_injuries(run_ts)
    if curr_injuries is not None:
        store.save_injuries(run_ts, curr_injuries)

    splits = _safe_splits(now)
    store.save_splits(run_ts, splits)
    public = _public_pct(splits, settings)
    alerts = _detect(store, run_ts, quotes, prev_injuries, curr_injuries, public)
    new_alerts = [a for a in alerts if store.add_alert(
        a.signature, run_ts, a.signal.kind, a.signal.severity, a.event_id, a.book, a.market, a.text())]
    dispatch(new_alerts, settings.alerts_log, settings.telegram_token, settings.telegram_chat_id)

    n_events = len({q.event_id for q in quotes})
    store.record_run(run_ts, "ok", n_events, usage)
    log.info("Corrida OK: %d partidos, %d cotizaciones, %d alertas nuevas | créditos: "
             "esta llamada=%s, usados=%s, restantes=%s",
             n_events, len(quotes), len(new_alerts), usage.last, usage.used, usage.remaining)
    log.info("Splits de Action Network: %d mercados con %% de tickets", len(splits))
    _safe_dashboard(settings, store)


def _safe_dashboard(settings: Settings, store: Store) -> None:
    try:
        path = build_dashboard(store, settings)
        log.info("Dashboard actualizado: %s", path)
    except (OSError, ValueError) as exc:
        log.error("No se pudo generar el dashboard: %s", exc)


def run_once(settings: Settings, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    run_ts = now.replace(microsecond=0).isoformat()
    store = Store(settings.db_path)
    try:
        _run(settings, store, now, run_ts)
    finally:
        store.close()


def report(settings: Settings) -> None:
    store = Store(settings.db_path)
    try:
        runs = store.runs()
        remaining = store.last_remaining()
    finally:
        store.close()
    if not runs:
        log.info("Aún no hay corridas registradas.")
        return
    paid = [r for r in runs if r.credits_last is not None]
    spent = sum(r.credits_last for r in paid)
    avg = spent / len(paid) if paid else settings.credits_per_call
    statuses = ", ".join(f"{k}={v}" for k, v in Counter(r.status for r in runs).items())
    log.info("Corridas: %d (%s)", len(runs), statuses)
    log.info("Créditos gastados por el tracker: %d en %d llamadas de pago (promedio %.1f por corrida)",
             spent, len(paid), avg)
    now = datetime.now(timezone.utc)
    events, usage = fetch_events(settings.api_key)
    remaining = usage.remaining if usage.remaining is not None else remaining
    starts = [datetime.fromisoformat(ev["commence_time"]) for ev in events]
    runs = estimate_paid_runs(starts, now, now + timedelta(days=ESTIMATE_DAYS))
    per_day = runs * settings.credits_per_call / ESTIMATE_DAYS
    log.info("Proyección con el calendario de los próximos %d días: %d snapshots de pago, "
             "%.0f créditos/día, ~%.0f créditos/mes", ESTIMATE_DAYS, runs, per_day, per_day * DAYS_PER_MONTH)
    if remaining is not None and per_day > 0:
        log.info("Créditos restantes en la cuenta: %d (~%.1f días a este ritmo)", remaining, remaining / per_day)
