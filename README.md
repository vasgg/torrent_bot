# Torrent bot

A Telegram bot for saving `.torrent` files to `Movies` / `Series`, triggering a
host healthcheck, and tracking availability. Only users listed in `ADMIN_IDS` can
use the commands.

## Commands

- `/start` — help and system uptime.
- `/uptime` — system uptime, the number and total duration of downtimes lasting
  at least two minutes in the last 30 days, and observed server reboots.
- `/outages` — the latest 10 events with estimated start time, recovery time, and
  duration, plus uptime before each event. The complete history remains in SQLite.
- `/health` — creates `/triggers/health.run` for the existing host healthcheck.
- Send `.torrent` files — choose Movies / Series for a single file or a batch.

## Availability tracking

Every 60 seconds, the bot calls Telegram (`getMe`) and saves the time of the
successful check in SQLite. When connectivity returns, it compares the current
time with the last successful check. SQLite is part of Python's standard library;
no separate database service is required.

On every startup, the bot reports how long it was absent and the start and end
of that interval. The first launch starts observation: earlier outages cannot be
reconstructed. An internet outage without a process restart is also recorded,
with a notification sent after connectivity returns. Pending notifications are
stored in the database and retried. The recipient is the first entry in
`ADMIN_IDS`, as before. A crash between sending a notification and recording its
delivery may cause a duplicate notification.

Notifications and `/outages` also show `Previous uptime`: the time from the
previous recorded recovery/startup to the beginning of the next downtime. For the
first interval, it starts at the first observation. This measures the bot's
continuous observed availability, rather than the host's boot uptime: a bot
restart or a recorded connection outage starts a new period. Gaps below the
two-minute connection-outage threshold do not reset it.

For example, a notification can show:

```text
Server reboot (possible power outage)
Offline since: ~2026-10-06 09:00:00
Back online: 2026-10-06 12:00:00
Downtime: ~0d 3h 0m 0s (Asia/Tbilisi)
Previous uptime: ~15d 0h 0m 0s
```

This value is calculated from existing history, including older events; no database
migration is needed. Already queued notification text is preserved. Inconsistent
timestamps (for example, a clock adjustment placing a recovery after the next
outage start) are reported as unknown instead of a negative uptime.

Event types:

- **Server reboot (possible power outage)** — the Linux boot ID changed.
- **Bot restart, server stayed on** — only the bot restarted.
- **Orderly bot stop, server stayed on** — the bot received SIGINT/SIGTERM and
  the server did not reboot. This does not necessarily mean planned maintenance.
- **Connection to Telegram lost** — connectivity returned without a bot restart.
- **Bot restart, server boot unknown** — the boot ID is unavailable, e.g. on macOS.

For a home server without a UPS, a reboot is a useful indirect sign of a power
outage. However, a manual reboot looks the same. The internet connection or
Telegram itself can also fail. The bot therefore records availability and does
not claim to confirm power loss. Downtime includes server startup and waiting for
the internet. A changed boot ID only proves that a reboot occurred: multiple power
outages before connectivity returns produce a single interval.

Both ends of an interval are estimated from checks made a minute apart, plus
network request time (up to 10 seconds per request). Connection gaps shorter than
two minutes are omitted. Restarts are recorded regardless of duration; total
downtime includes intervals of at least two minutes, while the reboot count
includes every observed server reboot. Timestamps are stored in UTC and displayed
in `Asia/Tbilisi` by default (configurable).

Keep the server clock synchronized. When the clock moves backwards, the bot resets
the observation point without creating a negative interval. A forward clock jump
may look like downtime.

## Server setup

1. Create `.env` using `.env.example`. Set `TOKEN`, `ADMIN_IDS` (e.g. `[123456789]`),
   and `TORRENT_DIR` — the **absolute incoming directory on the host**.
   On `zima`, this is `/mnt/foundation/torrents/incoming`.
2. From the project directory, run:

   ```sh
   mkdir -p data
   docker compose up -d --build
   ```

3. Send `/uptime` and `/outages` to the bot.

Compose mounts incoming at `/mnt/foundation/torrents/incoming` and explicitly sets
that path inside the container. The host's `./data` directory is bind-mounted at
`/data`, so the database is directly accessible outside Docker. Creating `data`
before starting the container keeps the directory owned by your host user.

Rebuilding, recreating, or removing the container does not delete `./data`.
Keep this directory when updating or moving the project. Run only one bot instance
with the same token or database.

`restart: unless-stopped` restarts the container automatically. Docker itself must
also start when the host boots. The container can start without internet access:
the bot waits for Telegram. Compose enables an init process to forward shutdown
signals even during Python initialization. Dependencies are installed during the
image build; starting the bot does not need network access to install packages.
Docker logs are limited to three files of 10 MB each.

## Accessing the database without Docker

The database is created when the updated bot starts:

- On the host: `<project directory>/data/uptime.sqlite3`.
- On `zima`: `/home/vasg/projects/torrent_bot/data/uptime.sqlite3`.
- Inside the container: `/data/uptime.sqlite3`.

From the project directory on the host, use the SQLite CLI in read-only mode:

```sh
sqlite3 -readonly data/uptime.sqlite3
```

For example, list recent intervals with UTC timestamps and duration in minutes:

```sql
.headers on
.mode column
SELECT datetime(started_at, 'unixepoch') AS offline_since_utc,
       datetime(ended_at, 'unixepoch') AS back_online_utc,
       round((ended_at - started_at) / 60.0, 1) AS minutes,
       kind
FROM outages
ORDER BY id DESC
LIMIT 20;
```

`outages` contains completed downtime intervals, `state` contains the latest
observation, and `notifications` contains messages awaiting delivery.

To inspect the database on another computer, first create a consistent snapshot
on the host using SQLite's backup command, then copy that snapshot:

```sh
# On zima, from /home/vasg/projects/torrent_bot:
sqlite3 -readonly -cmd '.timeout 5000' data/uptime.sqlite3 '.backup data/uptime-backup.sqlite3'

# On your computer:
scp zima:/home/vasg/projects/torrent_bot/data/uptime-backup.sqlite3 ./uptime-backup.sqlite3
```

The backup command uses SQLite's [online backup mechanism](https://sqlite.org/backup.html)
and works while the bot is running. Use it instead of copying the live database
file during writes. Open the snapshot with any SQLite viewer.
The `data` directory is excluded from Git and the Docker build context.

## Local development and checks

Requires Python 3.12+ and uv:

```sh
uv sync --locked
uv run python -m src.main
```

For a local run, `TORRENT_DIR` is the actual local directory. `UPTIME_DB` defaults
to `data/uptime.sqlite3`, and `UPTIME_TIMEZONE` defaults to `Asia/Tbilisi`.
Compose always uses `/data/uptime.sqlite3` inside the container.
On macOS, `/proc/uptime` and the boot ID are unavailable: system uptime cannot be
reported, but availability history still works.

```sh
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv audit --locked
```

Tests use temporary databases and a mocked Telegram API; they never send messages
to the real bot. They cover outages and restarts, notification retries, history,
command access, shutdown, and safe publication of downloaded files.

Torrents are downloaded to a temporary `.part` file in incoming, then published
atomically without overwriting an existing file. The incoming filesystem must
support hard links (regular ext4/btrfs filesystems do). A sudden power loss can
leave an incomplete `.part` file; configure the torrent client to watch only
`.torrent` files. The unused `legacy` directory has been removed.
