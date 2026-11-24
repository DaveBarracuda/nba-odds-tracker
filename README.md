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

## Varias casas (comparador)

Por defecto se cotizan **10 casas**: DraftKings, FanDuel, BetMGM, Caesars, ESPN BET, Fanatics, BetRivers,
Hard Rock, Circa y BetOnline (variable `BOOKMAKERS`; hasta 10 casas cuestan lo mismo que una: 3 créditos).

- Las alertas de movimiento (RLM, steam, gradual, información, ruido) se calculan sobre la **línea de consenso**
  (mediana de las casas), así no se repiten por casa. Steam/información indican cuántas casas se movieron igual.
- **Línea fuera de mercado**: una casa se separa del consenso ≥ 1 pt (spread), ≥ 1.5 (total) o ≥ 3 pp (moneyline)
  con al menos 4 casas cotizando; dice en qué lado está el valor.
- Dashboard: por partido, tabla desplegable con las 10 casas y ★ en el mejor precio de cada lado.
- Almacenamiento: solo se guardan las cotizaciones que cambian (la historia se reconstruye rellenando hacia
  adelante) y se borran partidos de hace más de `RETENTION_DAYS` (30 por defecto) y lesiones de más de 2 días.

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

## GitHub Actions (corre 24/7 sin tu PC)

`.github/workflows/tracker.yml` corre con **cron cada 30 min**, un snapshot por corrida: ejecuta
`python -m tracker run` (que decide si toca snapshot de pago) y guarda en la rama `data`. Cada job dura
1-2 minutos.

**Por qué ya no hay loop.** Hasta el 2026-11-24 este workflow corría en un job de 5.5 h que dormía entre
snapshots y al terminar se relanzaba a sí mismo con `workflow_dispatch`. Eso ocupaba un runner las 24 h del
día de forma perpetua, que es consumo excesivo de Actions: **el repo gemelo `nfl-odds-tracker` quedó
bloqueado por ToS ese día**. El loop se cambió por cron normal para no repetirlo aquí.

- Repo **público**: los minutos de Actions son gratis e ilimitados. La API key vive en el secret `ODDS_API_KEY`.
- Opcionales: secrets `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`; vars `BOOKMAKERS`, `MARKETS`, `MIN_CREDITS_REMAINING`.
- La rama `data` guarda `odds.sqlite3`, logs y el dashboard en un único commit reescrito en cada corrida.
- Cambios de código en `main` se aplican en la siguiente corrida (≤ 30 min).
- **La cadencia depende de la puntualidad de GitHub.** Los disparos programados se pueden retrasar; cuando un
  hueco pasa de 2.5 h el tracker avisa por Telegram. La cadencia se degrada, no se rompe. Si los retrasos
  resultan inaceptables, la alternativa es un cron de verdad en un servidor propio.
- Detener: `gh workflow disable tracker.yml`. Reanudar: `gh workflow enable tracker.yml`.

## Dashboard

Publicado con GitHub Pages desde la rama `data`: https://davebarracuda.github.io/nba-odds-tracker/

Cada corrida regenera `data/dashboard.html` (autocontenido, se recarga cada 5 min): alertas de las
últimas 24 h, y por partido la línea actual de DraftKings, apertura → actual, sparkline de la historia,
barras de tickets y dinero de Action Network, y un aviso cuando el dinero supera a los tickets en ≥10 pp.
