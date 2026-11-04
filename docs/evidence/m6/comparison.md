# M6 — k6 load test: 2 replicas vs 3 replicas

Date: 2026-11-04. All numbers below are copied from the raw k6 output in
[run-2-replicas.txt](run-2-replicas.txt) and [run-3-replicas.txt](run-3-replicas.txt),
or from PostgreSQL / NGINX / Prometheus queries run immediately around each test.

Visual evidence: [grafana-dashboard.png](grafana-dashboard.png) — the "Task API (local)" Grafana
dashboard for 2026-11-04 12:00:30–12:07:00 IST (06:30:30–06:37:00 UTC), captured by the operator.
It shows the two load bursts (Run A, then Run B) in the request-rate and p95 panels, the
healthy-targets panel at 3 at the end of the window, and "No data" in the 5xx panel. The screenshot
is supporting evidence only; all numbers in this report come from the text files above.

## Configuration (identical for both runs except replica count)

| Setting | Run A | Run B |
|---|---|---|
| App replicas | **2** | **3** |
| Virtual users (VUs) | 10 | 10 |
| Duration | 60s | 60s |
| Script | `load-tests/baseline.js` (create → read → delete, 0.2s sleep) | same |
| Target | `http://host.docker.internal:8081` (NGINX) | same |
| k6 | `grafana/k6:latest` = k6 v2.3.0, image `3e4186aea62e` | same |
| Time window (UTC) | 06:31:40 → 06:32:47 | 06:34:53 → 06:35:59 |
| Host | Docker Desktop, 4 vCPU, ~8 GB, no per-container CPU/memory limits | same |

Command (both runs):

```
docker run --rm -v <project>/load-tests:/scripts:ro grafana/k6:latest run --no-color -e VUS=10 -e DURATION=60s /scripts/baseline.js
```

Before Run B, the app was scaled with `docker compose up -d --scale app=3 --no-build`,
and the run started only after all 3 replicas were `healthy` and Prometheus showed 3/3 targets `up`
(plus a 20s pause).

## Results

| Metric | Run A (2 replicas) | Run B (3 replicas) | Change |
|---|---|---|---|
| Iterations | 675 (11.16/s) | 969 (15.99/s) | +43.6% |
| HTTP requests | 2025 (33.49/s) | 2907 (47.96/s) | +43.6% |
| Checks passed | 2025 / 2025 (100.00%) | 2907 / 2907 (100.00%) | — |
| Checks failed | 0 | 0 | — |
| `http_req_failed` | 0.00% (0 of 2025) | 0.00% (0 of 2907) | — |
| Avg response time | 227.8 ms | 138.81 ms | −39.1% |
| Median response time | 198.9 ms | 108.19 ms | −45.6% |
| p90 response time | 385.01 ms | 262.02 ms | −31.9% |
| p95 response time | **477.86 ms** | **343.26 ms** | −28.2% |
| Max response time | 1.19 s | 1.46 s | worse |
| Avg iteration duration | 892.74 ms | 623.54 ms | −30.2% |
| Threshold `p(95)<500` | ✓ passed (close to limit) | ✓ passed | — |
| Threshold `rate<0.01` | ✓ passed | ✓ passed | — |

## PostgreSQL cleanup verification

Measured with `select count(*) from tasks` and `tasks_id_seq.last_value`.

| | Rows before | Rows after | Sequence before | Sequence after | Tasks created (seq delta) | NGINX `201` responses |
|---|---|---|---|---|---|---|
| Run A | 0 \* | 0 | 1450 \* | 2125 | 675 | 675 |
| Run B | 0 | 0 | 2125 | 3094 | 969 | 969 |

Result: every task created by k6 was deleted. The sequence delta equals the number of
iterations and of `201`, `200` and `204` responses in each run, and the row count returned to 0.
No data was reset or deleted manually; the sequence was not reset.

\* Run A's "before" value was taken by a pre-check a few minutes before the run (the run's own
before-query failed because of a quoting bug in the helper script). Only `/health` and `/ready`
requests, which do not create tasks, were made in between. Run A's "after" and both Run B values
were taken immediately around the runs.

## NGINX upstream distribution

Counted from NGINX access-log lines (`upstream=` field) for `/tasks` requests inside each run's
time window. These counts match k6's `http_reqs` exactly.

| Replica (container / IP) | Run A | Run B |
|---|---|---|
| `ed1eedaff323` / 172.18.0.3 | 1000 (49.4%) | 1010 (34.7%) |
| `ed2e07360739` / 172.18.0.4 | 1025 (50.6%) | 948 (32.6%) |
| `4006b6489f48` / 172.18.0.8 (new) | — | 949 (32.6%) |
| **Total** | **2025** | **2907** |

NGINX status codes in the windows: Run A 675×201, 675×200, 675×204; Run B 969×201, 969×200, 969×204.
No 4xx/5xx.

Prometheus (`increase(app_http_requests_total{path=~"/tasks.*"})` per instance) shows the same
pattern — Run A ≈ 1035 / 979, Run B ≈ 961 / 1097 / 956 — but `increase()` extrapolates over the
window, so the NGINX counts above are the exact figures.

## Analysis

- **Error rate:** acceptable in both runs: 0 failed requests, 100% checks passed.
- **Load balancing:** NGINX spread traffic close to evenly across every available replica,
  and the new third replica received its share (~33%) without restarting NGINX.
- **Latency:** in this measurement, 3 replicas had lower average (−39%) and p95 (−28%) latency.
  The worst single request (max) was slower in Run B.
- **Throughput:** rose 43.6%. Note that this test is a *closed model*: each VU waits for a response
  before sending the next request, so throughput is a consequence of latency here, not an
  independent result. Faster responses → more iterations in the same 60 seconds.
- **CPU explanation — corrected after measurement (see "Measured CPU usage" below).** The first
  version of this report offered an *unverified assumption*: that each single-process Uvicorn
  replica was limited to about one CPU core, so a third process could use capacity the first two
  could not. The CPU data measured afterwards does **not** support that: no replica came close to
  one full core. The cause of the latency difference is therefore **not established**; contention
  for the shared 4 vCPUs (k6, PostgreSQL, NGINX and the replicas all run on the same laptop) and
  run-order effects remain possible explanations, but neither was measured.

## Measured CPU usage (added after the runs)

Source: Prometheus history of `rate(process_cpu_seconds_total{job="app"}[30s])`, queried after the
runs for a 30-second window around the middle of each run (Run A at 06:32:15Z, Run B at 06:35:30Z).
Each value is the CPU used by one replica's Uvicorn process, in cores (1.0 = one full core).
These are mid-run snapshots, not averages over the whole run, and they cover only the app
processes — not PostgreSQL, NGINX or k6.

| Replica | Run A (2 replicas) | Run B (3 replicas) |
|---|---|---|
| `ed2e07360739` / 172.18.0.4 | 0.564 cores | 0.337 cores |
| `ed1eedaff323` / 172.18.0.3 | 0.527 cores | 0.405 cores |
| `4006b6489f48` / 172.18.0.8 | — | 0.402 cores |
| **All app replicas** | **≈ 1.09 cores** | **≈ 1.14 cores** |

What this shows: in Run A, when p95 was close to the 500 ms limit, each replica used only about
half a core. So the replicas were **not** each saturating a CPU core. Total app CPU was about the
same in both runs (≈ 1.1 of 4 vCPUs); in Run B it was simply spread across three processes.

## Limitations

- **One run per configuration.** No repetitions, so run-to-run noise is unknown. A previous
  (unsaved) 10 VU / 30s run with 2 replicas reported p95 ≈ 230 ms, far better than Run A's
  477.86 ms, which suggests the variance between runs on this laptop is large.
- **Order effect not controlled.** Run B always ran second, after Run A had warmed up PostgreSQL
  and the app. A fair follow-up is A → B → A (or alternating several times).
- **Everything shares one laptop.** k6, NGINX, 3 app containers, PostgreSQL, Prometheus and
  Grafana all compete for the same 4 vCPUs. k6 itself consumes CPU that the app could use.
- **Single database.** All replicas share one PostgreSQL instance, so adding app replicas cannot
  scale past the database's limit.
- **Small, fixed load.** 10 VUs with a 0.2s sleep is a light load; this is not a capacity test and
  does not find the breaking point.

## Conclusion

Both configurations met the performance targets (p95 < 500 ms, errors < 1%) with zero failures,
NGINX balanced requests across all replicas, and the test cleaned up every task it created.
In this single pair of runs, 3 replicas were faster (p95 343.26 ms vs 477.86 ms) and completed
43.6% more iterations, but with one run each and an uncontrolled order effect, this should be
read as "3 replicas performed better in this measurement", not as a proven general rule.
