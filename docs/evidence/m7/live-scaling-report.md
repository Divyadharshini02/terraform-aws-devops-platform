# M7 Phase 3 — Live autoscaling validation

Status: **complete.** Run 1 (10 VUs) was stopped because of unhealthy containers and is
recorded below as an incident. Run 2 (5 VUs) completed the full cycle 1 → 2 → 3 → 2 → 1
with 0 failed requests. All numbers below are copied from the evidence files listed in
each section; nothing is estimated unless marked.

## Environment and configuration

| Item | Value |
|---|---|
| Host | Windows 10, Docker Desktop, Docker Engine 29.7.2 (API 1.55), 4 vCPU, ~7.7 GB, no per-container limits |
| App | FastAPI, 1 Uvicorn process per replica, Compose healthcheck `python -c urllib…/ready` (urlopen timeout 2s, Docker timeout 3s, interval 5s, retries 6, start_period 5s) |
| Autoscaler | `autoscaler` Compose service, profile `autoscale`, `AUTOSCALER_DRY_RUN=false`, read-only FS, `cap_drop: ALL`, `no-new-privileges`, Docker socket mount, no ports |
| Policy | min 1, max 3 · scale out when per-replica business req/s > 10 for 3 checks · scale in when < 4 for 6 checks and projected < 7 · cooldown 60s out / 90s in (directional, only after success) · check every 10s · `rate()[30s]` · health wait 60s · stop timeout 10s · backoff 10→60s |
| Load | `load-tests/baseline.js` via `grafana/k6:latest` (k6 v2.3.0) to `http://host.docker.internal:8081` (create → read → delete, 0.2s sleep) |
| Starting state | 1 replica (`app-1`, ed2e07360739), PostgreSQL 0 rows, `tasks_id_seq` last_value 3094 |

## Test sequence

1. Scale 3 → 1 with `docker compose up -d --no-deps --scale app=1 app` (previewed with `--dry-run`; removed app-3 and app-2, kept app-1 unchanged).
2. Start autoscaler live: `AUTOSCALER_DRY_RUN=false docker compose --profile autoscale up -d --no-deps autoscaler`. Logged `dry_run=false` and `reason=at_min` while idle.
3. **Run 1:** k6, 10 VUs, planned 6 min — stopped after 2m13s (see Incident).
4. Recovery: cleaned up 9 incomplete test records, returned to 1 replica.
5. **Run 2:** autoscaler restarted live; k6 5 VUs for the full 6 min (scale-out), then k6 ended
   and a read-only `GET /tasks` probe ran during scale-in. A background watchdog checked the
   stop conditions every 5 s (controller errors, unhealthy containers, NGINX 5xx, > 5 DB rows).
6. Final validation: probe, watchdog and autoscaler stopped; state compared with pre-flight.

## Incident — Run 1 (10 VUs): new replica unhealthy, scale-out failed

Evidence files: [k6-scale-out.txt](k6-scale-out.txt) (raw k6 output, unedited),
[autoscaler-run1-10vu.log](autoscaler-run1-10vu.log) (raw controller log).
Times are UTC on 2026-11-04.

### Timeline

| Time | Observed |
|---|---|
| 07:50:15 | Load visible: `total_rps=5.61` |
| 07:50:25 / :35 / :45 | High streak 1 → 2 → 3 at 15.73 / 27.34 / 28.98 req/s per replica |
| 07:50:45 | `action=scale_out reason=sustained_high_load replicas=1 desired=2` |
| 07:50:47 | `replica_created name=…-app-2` (first attempt, IP 172.18.0.8) |
| 07:50:5x–07:51:0x | ~180 NGINX requests went to the new replica first and were retried on app-1 (`upstream=172.18.0.8:8000, 172.18.0.4:8000`) |
| 07:51:45 | **2 × HTTP 502** on `POST /tasks` from the new replica (`rt` 0.015s and 0.076s) |
| 07:51:49 | New replica reported `unhealthy` (~62s after creation). Controller removed it: `scale_failed … detail='container reported unhealthy' consecutive_failures=1 retry_in_s=10`. No cooldown started. |
| 07:51:50 / 07:52:00 | `no_healthy_replica action=hold running=1` — app-1 was also not `healthy` at these checks |
| 07:52:09 | app-1 healthy again, backoff expired → second `scale_out` attempt |
| 07:52:12 | `replica_created name=…-app-2` (0a60d0030cfe, same IP 172.18.0.8) |
| 07:52:20 | Operator stopped k6 (`docker stop -t 30`) and the autoscaler (`docker compose stop`) |
| 07:52:23 | k6: `test run was aborted because k6 received a 'terminated' signal` |
| 07:52:27 → | app-1 health checks pass again (`FailingStreak=0`); second app-2 became `healthy` once load stopped |

### k6 results (from the raw output, run aborted at 2m13.7s of 6m)

| Metric | Value |
|---|---|
| HTTP requests | 3577 (26.75/s) |
| `http_req_failed` | 0.05% (2 of 3577) — ✓ threshold `rate<0.01` |
| Checks | 3575 / 3577 passed — `create is 201` failed twice |
| p95 / p90 / avg response time | **606.99 ms** ✗ threshold `p(95)<500` · 489.28 ms · 303.3 ms |
| Iterations | 1191 |

### NGINX (07:50:00–07:52:30, access-log `upstream=` lines)

`status=200` 1192 · `201` 1195 · `204` 1190 · **`502` 2** · `499` 7.
The 7 × 499 ("client closed request") are requests in flight when k6 was stopped.

### Root cause — evidence vs hypothesis

**Observed (evidence):**
- app-1's Docker health log shows `Health check exceeded timeout (3s)` at 07:52:19, while NGINX
  was still logging successful 200/201/204 responses from it. The service was answering requests,
  but its healthcheck probe did not complete within 3 s under this load.
- The new replica received traffic as soon as it started: Docker DNS registers the `app` alias at
  container start, and NGINX does not consult Docker health status. NGINX logs show requests to
  172.18.0.8 being retried on app-1 during its start-up.
- The first new replica was marked `unhealthy` ~62 s after creation and removed by the controller.
- The two 502s came from the first new replica at 07:51:45, about 4 s **before** the controller
  removed it. (An earlier verbal assessment linked them to the removal; the timestamps show they
  preceded it.)

**Hypotheses (not proven):**
- The healthcheck starts a new Python interpreter for every probe; with k6, the replicas and the
  start-up of a new container competing for 4 vCPUs, the probe exceeded its 2 s/3 s timeouts.
  CPU usage was not recorded during the run.
- The exact cause of the two 502s (e.g. a connection closed by the overloaded replica) is not
  established; the NGINX error log for that window was not examined.

### Autoscaler safety behaviour during the incident

| Safeguard | Observed |
|---|---|
| Consecutive high checks | Scale-out only after the 3rd high check |
| Failed replica cleanup | Unhealthy new replica removed (`failed_replica_removed`) |
| No cooldown after failure | Confirmed: `retry_in_s=10`, retry at 07:52:09 |
| Backoff | First retry delayed 10 s as designed |
| No healthy replica → hold | `no_healthy_replica action=hold` with recovery instruction; no container created |
| Graceful shutdown | **Not graceful during an in-progress scale-out:** the controller was in its 60 s health wait, did not react to SIGTERM, and Docker killed it after 10 s (`Exited (137)`, no `event=stopped`) |

**Design limitation exposed:** the controller only decides when *all* replicas are healthy and
only clones a *healthy* replica. These are deliberate safety rules, but when overload makes the
only replica fail its healthcheck, the controller cannot scale out at the moment it is most needed.

### Incomplete test records and cleanup

Stopping k6 mid-iteration left **9 rows** (`id` 4285–4293, all `title='load test'`), created by
this run and never deleted. No pre-existing data was involved (0 rows before the run).
Sequence: 3094 → 4293 (1199 values used; 1195 `201` responses plus aborted in-flight creates).

Cleanup (approved): each id was re-verified as `load test`, deleted with `DELETE /tasks/{id}`
(all 9 → `204`, then `GET` → `404`). Result: **0 rows**, `last_value` **4293** (sequence not reset).

### Recovery

`docker compose up -d --no-deps --scale app=1 app` (previewed with `--dry-run`) removed only
the controller-created app-2 (0a60d0030cfe). app-1 kept its original start time and 0 restarts.
Afterwards: app-1 healthy, NGINX 200 on `/health` `/ready` `/tasks`, PostgreSQL ready,
Prometheus 1/1 target up.

## Run 2 — 5 VUs: full scale-out and scale-in cycle

Evidence files: [k6-run2-5vu.txt](k6-run2-5vu.txt) (raw k6 output, unedited),
[autoscaler-run2-5vu.log](autoscaler-run2-5vu.log) (raw controller log from its restart),
[probe-scale-in.txt](probe-scale-in.txt) (raw probe output). Times are UTC on 2026-11-04.

### Expected vs actual

| Step | Expected | Actual |
|---|---|---|
| Start | 1 replica, `at_min`, `dry_run=false` | ✓ 08:07:55 `event=started … dry_run=false`; `reason=at_min replicas=1` |
| 1 → 2 | Scale out after 3 high checks (> 10) | ✓ 10.44 / 24.52 / 32.32 req/s/replica → `scale_out` 08:12:15; `app-2` (bac761390c61) created 08:12:17, **healthy and `scale_succeeded` 08:12:24 (7 s)** |
| Scale-out cooldown | No further scale-out for 60 s | ✓ `reason=scale_out_cooldown` on 6 checks (08:12:25–08:13:15) |
| 2 → 3 | 3 *fresh* high checks after cooldown | ✓ 18.92 / 17.81 / 17.26 at 08:13:25 / :35 / :45 → `scale_out`; `app-3` (56812557a464) created 08:13:46, **`scale_succeeded` 08:13:53 (7 s)** |
| Max = 3 | Hold at 3 under continued high load | ✓ 24 checks at 3 replicas (08:13:55–08:17:45): 17 × `reason=at_max` (10.11–11.77 req/s/replica) and 7 × `reason=within_target` (7.92–9.93); no 4th replica attempted |
| k6 | Full 6 min | ✓ ended 08:17:42 (`default ✓ [ 100% ] 5 VUs 6m0s`) |
| 3 → 2 | 6 low checks (< 4) and projected < 7 | ✓ low streak 1→6 at 08:18:05–08:18:55 → `scale_in`; **highest number `app-3` stopped** (`timeout_s=10`) and removed, `scale_succeeded` 08:18:56 |
| Scale-in cooldown | No further scale-in for 90 s | ✓ `reason=scale_in_cooldown` 08:19:05–08:20:25 |
| 2 → 1 | 6 fresh low checks after cooldown | ✓ low streak 1→6 at 08:20:35–08:21:25 (0.22–0.26 req/s/replica, projected 0.44) → `scale_in`; **`app-2` stopped** and removed, `scale_succeeded` 08:21:28 |
| Min = 1 | Never remove the last replica | ✓ `reason=at_min replicas=1` on all 13 checks 08:21:35–08:23:35 |
| Shutdown (idle) | Graceful | ✓ stop issued 08:23:44 → `event=stopped` 08:23:45, `Exited (0)` |

Controller log totals (run 2): 4 × `scale_succeeded`, 2 × `replica_created`, 2 × `replica_stopping`,
**0** × `scale_failed`, `scale_skipped`, `no_healthy_replica`, `check_skipped`, `tick_error`.

### k6 results (full 6-minute run)

| Metric | Value |
|---|---|
| HTTP requests | 11460 (31.83/s), **0 failed** — ✓ `rate<0.01` |
| Checks | 11460 / 11460 (100%): create 201, read 200, delete 204 |
| Response time | avg 89.45 ms · med 58.46 ms · p90 180.23 ms · **p95 244.17 ms** ✓ `p(95)<500` · max 2.56 s |
| Iterations | 3820 |

### NGINX and Prometheus

| Window | Observation |
|---|---|
| 2 replicas (08:12:24–08:12:48) | 369 requests → app-1, 340 → app-2 |
| 3 replicas (08:13:53–08:15:50) | 1173 → app-1, 1165 → app-2, 1121 → app-3 (≈ 33% each) |
| Whole run | **0 × 5xx** (watchdog checked every 5 s). 25 requests were first sent to a replica that was still starting and were retried successfully on another replica (same mechanism as Run 1, no errors this time) |
| After each scale-in | 0 requests to the removed replica's IP |
| Prometheus | Targets followed the replica count: 1 → 2 → 3 → 2 → 1, all `up` |

### Scale-in probe

Read-only `GET /tasks` from the host, 08:19:08–08:23:33: **121 requests, all HTTP 200**,
max 0.082 s (30 answered by app-2, 91 by app-1). It covered the **2 → 1** transition
(200s throughout 08:21:25–08:21:28) but **not 3 → 2**, which happened at 08:18:56, before the
probe's first request. The loop ran at ≈ 0.46 req/s, not the planned 1 req/s, because
each iteration's process start-up is slow in Git Bash on Windows.

### Database

Rows returned to 0 after k6 finished. Sequence 4293 → **8113** (+3820 = exactly the number of
iterations, i.e. every created task was deleted). The probe only reads.

## Replica count timeline

| Time (UTC) | Replicas | Event |
|---|---|---|
| before 07:44:11 | 3 → 1 | Manual, starting state (exact time not logged; done just before the autoscaler started at 07:44:11) |
| 07:50:47–07:51:49 | 1 (+1 starting, failed) | Run 1 first scale-out attempt |
| 07:52:12 | 1 (+1 starting) | Run 1 second attempt, controller stopped mid-wait |
| after 07:52:23 | 2 → 1 | Manual recovery (exact time not logged) |
| 08:12:24 | 1 → 2 | Run 2 autoscaler scale-out |
| 08:13:53 | 2 → 3 | Run 2 autoscaler scale-out |
| 08:18:56 | 3 → 2 | Run 2 autoscaler scale-in (app-3) |
| 08:21:28 | 2 → 1 | Run 2 autoscaler scale-in (app-2) |

## Grafana observations

The operator watched the "Task API (local)" dashboard during Run 2 and captured three screenshots.
The dashboard shows local time (IST = UTC + 5:30). Panels: request rate by instance
(`rate(app_http_requests_total[1m])`, all paths), p95 latency, 5xx error rate, and healthy app
targets (`count(up{job="app"} == 1)`).

| Screenshot | Time window shown | What it shows |
|---|---|---|
| [01-baseline.png](01-baseline.png) | Last 15 min, ≈ 13:25–13:39 IST (≈ 07:55–08:09 UTC) | Baseline before Run 2's load: **Healthy app targets = 1**, with the panel history showing the earlier drop to 1 target. Request rate: `ed2e07360739` (app-1) steady at ≈ 0.2 req/s; the `0a60d0030cfe` line (the Run 1 replica removed during recovery) ends early in the window. p95 below 0.125 s; 5xx panel "No data". |
| [03-scale-out-2-to-3.png](03-scale-out-2-to-3.png) | Last 5 min, ≈ 13:42–13:47 IST (≈ 08:12–08:17 UTC) | Scale-out under 5 VUs: **Healthy app targets = 3**, the step graph rising from 2 to 3 targets. Request rate: `ed2e07360739` starts near 27 req/s and falls as `bac761390c61` (app-2) and then `56812557a464` (app-3) appear; the three lines converge around 10 req/s each. p95 ≈ 0.19–0.25 s; 5xx panel "No data". |
| [04-scale-in-3-to-2.png](04-scale-in-3-to-2.png) | Last 5 min, ≈ 13:46–13:51 IST (≈ 08:16–08:21 UTC) | Scale-in after k6 ended (13:47:42 IST): request rate of all three instances drops from ≈ 10–11 req/s to near 0 by ≈ 13:49; p95 falls from ≈ 0.22 s to below 0.05 s; **Healthy app targets = 2**, the step graph dropping from 3 to 2 targets. 5xx panel "No data". |

The baseline rate of ≈ 0.2 req/s on an idle replica is consistent with the Docker healthcheck
calling `/ready` every 5 s (this panel counts all paths) — an interpretation, not a separate
measurement. The screenshot timings agree with the controller log: 2 → 3 `scale_succeeded` at
08:13:53 UTC (13:43:53 IST) and 3 → 2 at 08:18:56 UTC (13:48:56 IST).

**No screenshot exists for the 2-replica state of the scale-out** (the 2 → 3 step followed about
90 s after 1 → 2, before the checkpoint) **or for the 2 → 1 scale-in**; those transitions are
documented by the controller log, Docker and Prometheus only.

## Safeguard validation

| Safeguard | How validated | Result |
|---|---|---|
| Maximum 3 replicas | Live (Run 2) + unit tests | ✓ 17 × `at_max` while above threshold, no 4th replica |
| Minimum 1 replica | Live (Run 2) + unit tests | ✓ `at_min`, last replica never removed |
| Scale-out cooldown 60 s | Live (Run 2) + unit tests | ✓ 6 cooldown holds; next scale-out needed 3 fresh checks |
| Scale-in cooldown 90 s | Live (Run 2) + unit tests | ✓ 9 cooldown holds; next scale-in needed 6 fresh checks |
| Directional cooldowns | Unit tests only | ✓ (not exercised live) |
| Consecutive high/low checks | Live (Run 2) + unit tests | ✓ 3 high / 6 low before each action |
| Prometheus unavailable → hold | Unit tests only | ✓ |
| Zero healthy replicas → hold, no creation | Live (Run 1) + unit tests | ✓ `no_healthy_replica action=hold`, no container created |
| Docker failure → no cooldown, backoff | Live (Run 1: 1 failure, 10 s backoff, retry) + unit tests (10 → 20 → 40 → 60 s) | ✓ |
| Manual replica-count change → skip | Unit tests only (live test skipped by operator decision) | ✓ |
| Graceful shutdown | Live | ✓ idle (`Exited (0)`, `event=stopped`); ✗ **during a scale-out health wait** (Run 1: killed, `Exited (137)`) |
| No 5xx during normal scaling | Live | ✓ Run 2: 0 × 5xx; ✗ Run 1 (overload): 2 × 502 |

Unit tests at the end of testing: `134 passed`. No application, autoscaler, healthcheck or Compose
change was made during Phase 3.

## Safety verification (final state vs pre-flight snapshot)

| Item | Pre-flight (07:40 UTC) | Final (08:24 UTC) |
|---|---|---|
| app-1 | ed2e07360739, started 06:18:54, 0 restarts | **identical** |
| app-2 / app-3 | running | removed (planned scale to 1) |
| db, nginx, prometheus, grafana | IDs, start times, 0 restarts | **identical** |
| autoscaler | stopped (93c092491410) | stopped, `Exited (0)` (7c25f4392490, recreated when DRY_RUN changed) |
| PostgreSQL rows | 0 | **0** |
| `tasks_id_seq` | 3094 | 8113 (+5019 = 1199 Run 1 + 3820 Run 2; never reset) |
| `pgdata` volume | created 2026-10-24 | **same volume** |
| Health | — | app-1 healthy, NGINX 200 on `/health` `/ready` `/tasks`, PostgreSQL ready, Prometheus 1/1, Grafana 200 |
| Leftovers | — | no k6 container, no extra app containers |
| Secrets | — | 0 matches of any `.env` credential or `DATABASE_URL` in all M7 evidence files |

## Limitations

- **One run per load level.** Run 1 (10 VUs) and Run 2 (5 VUs) used different loads, so they are
  not a like-for-like performance comparison, and nothing here shows that more replicas are
  generally faster.
- Everything (k6, NGINX, replicas, PostgreSQL, monitoring) shares one 4-vCPU laptop.
- **Healthcheck under load (open):** at 10 VUs the healthcheck probe timed out while the app was
  still serving traffic. Not fixed in this phase.
- **Traffic before healthy (open):** new replicas receive NGINX traffic as soon as they start;
  Run 2 needed 25 retries, Run 1 had 2 × 502 around the failing replica.
- **Controller limitation (open):** it cannot scale out when the only replica fails its
  healthcheck, by design of its safety rules.
- **Shutdown during a scale-out is not graceful** (Docker kills the controller after 10 s).
- The scale-in probe missed 3 → 2 and ran at ≈ 0.46 req/s.
- Screenshots cover the baseline, 2 → 3 and 3 → 2 only; there is none of the 2-replica state or
  of the 2 → 1 transition.
- **Credential rotation pending:** the database password exposed during Phase 2 must be rotated
  before anything is pushed.

## Conclusion

In Run 2 the controller completed the full cycle **1 → 2 → 3 → 2 → 1** on live containers, with
every decision, cooldown and boundary behaving as designed, new replicas healthy within 7 s,
NGINX and Prometheus following each change, **0 failed requests and 0 × 5xx**, and the database
returned to 0 rows. Run 1 showed that at higher load (10 VUs) the single replica's healthcheck
fails before the controller can add capacity; the controller's safety rules held (failed replica
removed, no cooldown, backoff, hold with no healthy replica), but 2 requests failed with 502.
These are observations from one run per load level on one laptop, not statistically
established results.
