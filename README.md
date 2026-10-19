# NBA Odds Movement Tracker

Toma un snapshot de odds NBA (The Odds API) cada hora, guarda la historia en SQLite
y alerta sobre 5 tipos de movimiento.

## Uso

```
python -m tracker run      # una corrida (lo que ejecuta la tarea programada)
python -m tracker loop     # corre cada hora en primer plano
python -m tracker report   # créditos gastados y proyección mensual
python -m tracker dashboard  # regenera data/dashboard.html sin gastar créditos
python -m pytest -q        # tests
powershell -ExecutionPolicy Bypass -File register_task.ps1   # alternativa local a GitHub Actions
```

Configuración en `.env` (ver `.env.example`). Salidas en `data/`: `odds.sqlite3`, `alerts.log`, `tracker.log`.

## Tipos de alerta

Spreads/totals se miden en puntos; moneyline en puntos porcentuales de probabilidad sin vig del local.
Umbrales en `tracker/detectors.py` (`THRESHOLDS`).

| Tipo | Regla | Severidad |
|---|---|---|
| Por Información | salto ≥ umbral steam en ≤2.5h **y** cambio en el reporte de lesiones de ESPN para alguno de los equipos; o salto ≥ umbral info (spread 2, total 3, ML 6pp) aunque no se detecte noticia | Alta |
| Steam | salto ≥ umbral steam (spread 1, total 1.5, ML 3pp) en ≤2.5h sin noticia detectada | Alta |
| RLM | ≥60% de tickets en un lado y la línea se movió en contra de ese lado desde el primer snapshot (spread 0.5, total 1, ML 1.5pp) | Alta |
| Gradual | ≥3 movimientos pequeños en la misma dirección (se permiten pausas) con acumulado ≥ spread 1 / total 1.5 / ML 3pp | Media |
| Ruido | solo cambia el juice, ajuste ≤ 0.5 pts (ML ≤1pp), o rebote que regresa al valor previo | Baja (no se envía a Telegram) |

## Limitaciones conocidas

- **Splits de tickets/dinero**: se toman cada corrida de Action Network (consenso, endpoint JSON
  público `web/v2/scoreboard/publicbetting/nba`, 4 días hacia adelante, sin costo de créditos) y se guardan
  en la tabla `splits`. RLM usa el % de tickets; `data/public_betting.csv` (opcional) tiene prioridad.
  Action Network solo publica splits cuando el partido ya recibe apuestas (≈1–3 días antes). Es un
  endpoint no documentado: si cambia, el tracker sigue corriendo y lo registra en el log.
- **Steam real** es un movimiento simultáneo en varias casas. Con una sola casa (DraftKings) se aproxima
  como salto brusco entre snapshots; al agregar más casas en `BOOKMAKERS` cada casa se evalúa por separado.
- El "open" para RLM es el primer snapshot que vio el tracker, no la apertura real.

## Frecuencia y créditos (The Odds API)

- Snapshot de pago **cada hora**; desde **2 h antes del primer juego del día** (fecha ET) hasta que arranca
  el último juego de ese día, **cada 30 min** (`tracker/schedule.py`). El cron dispara cada 30 min y las
  corridas que no tocan solo usan el endpoint de eventos, que es gratis.
- Costo por snapshot = nº de mercados × regiones (cada 10 casas = 1 región), sin importar cuántos partidos.
  DraftKings + h2h/spreads/totals = **3 créditos**.
- Un día típico son 29–34 snapshots → **~90–100 créditos/día, ~2,700–3,000/mes**.
  `python -m tracker report` calcula la proyección con el calendario real de los próximos 7 días.
- `MIN_CREDITS_REMAINING` detiene el tracker antes de agotar la cuenta.

## GitHub Actions

`.github/workflows/tracker.yml` corre a los minutos :05 y :35 de cada hora (el cron de GitHub puede retrasarse
unos minutos; la lógica tolera hasta 10 min). Necesita:

- Secret `ODDS_API_KEY` (opcionales `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).
- Variables opcionales `BOOKMAKERS`, `MARKETS`, `MIN_CREDITS_REMAINING`.
- La rama `data` guarda `odds.sqlite3`, logs y `dashboard.html` en un único commit que se reescribe en cada
  corrida (la historia vive en la base, no en git). Para verlos: `git fetch origin data && git checkout origin/data -- .`
  en un clon aparte, o descarga los archivos desde la rama `data` en GitHub.

## Dashboard

Cada corrida regenera `data/dashboard.html` (autocontenido, se recarga cada 5 min): alertas de las
últimas 24 h, y por partido la línea actual de DraftKings, apertura → actual, sparkline de la historia,
barras de tickets y dinero de Action Network, y un aviso cuando el dinero supera a los tickets en ≥10 pp.
