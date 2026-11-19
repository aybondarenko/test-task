# Incident Report: it_task_log (2).log

## Incident Summary

The log appears to show 2 degradation episodes, the first starting at 14:04:42 UTC, with 8 findings across 7 services. The likely causes, by severity, are: postgres-primary restart at 14:29:23 UTC (F5, critical); postgres-primary pool saturation at 14:05:02 UTC (F3, high). The episodes may be linked (H3). Recovery after E2 was observed, but the observation window is short.

## Key Findings

### F1 [high] (symptom) 51 connection error, timeout and connection pool saturation events recorded in order-service and payment-service.
- Services: order-service, payment-service; window: 2026-03-27 14:04:42.803Z to 2026-03-27 14:30:55.936Z; events: 51; DB categories: db_latency, pool_exhaustion, connection_errors
- Role rationale: Starts at 14:04:42; depends on postgres-primary, which show an upstream deviation in the same episode.
- Shift: order-service db_latency_ms baseline p95 16 -> worst 611 (line 501)
- Shift: order-service latency_ms baseline p95 89 -> worst 2144 (line 504)
- line 143 [WARN] order-service: 2026-03-27T14:04:42.803Z WARN order-service db acquire connection slow wait_ms=418 pool_in_use=29 pool_idle=1
- line 144 [WARN] payment-service: 2026-03-27T14:04:46.050Z WARN payment-service db query latency high op=select_payment_profile duration_ms=641 pool_in_use=27 pool_idle=2
### F2 [high] (symptom) 116 timeout, retry and queue backlog events recorded in billing-worker, inventory-service, notification-service, order-service and payment-service.
- Services: billing-worker, inventory-service, notification-service, order-service, payment-service; window: 2026-03-27 14:04:54.851Z to 2026-03-27 14:25:33.936Z; events: 116
- Role rationale: Starts at 14:04:54; depends on postgres-primary, which show an upstream deviation in the same episode.
- Shift: inventory-service lag_ms baseline p95 36 -> worst 1467 (line 330)
- Shift: billing-worker queue_depth baseline p95 2 -> worst 45 (line 277)
- line 148 [WARN] inventory-service: 2026-03-27T14:04:54.851Z WARN inventory-service request_id=req-84079 order_id=ord-51107 reservation write latency high sku=sku-22 lag_ms=412
- line 156 [WARN] billing-worker: 2026-03-27T14:05:09.633Z WARN billing-worker job_id=job-6111 invoice_id=inv-9111 attempt=1 retrying reason="payment-service latency high" backoff_ms=500 queu...
### F3 [high] (cause) 26 connection error and connection pool saturation events recorded in postgres-primary.
- Services: postgres-primary; window: 2026-03-27 14:05:02.164Z to 2026-03-27 14:20:14.936Z; events: 26; DB categories: server_saturation, connection_errors, aborted_transactions
- Role rationale: Starts at 14:05:02, within 30s of episode onset 14:04:42; it has no dependencies in the service graph.
- Shift: postgres-primary active_connections baseline p95 30 -> worst 100 at max_connections=100 (line 290)
- Shift: postgres-primary waiting_clients baseline p95 0 -> worst 31 (line 290)
- Composite signal: active_connections rose 30 → 100 while txn_per_s fell 107 → 51: connections appear to be held rather than serving load.
- line 152 [WARN] postgres-primary: 2026-03-27T14:05:02.164Z WARN postgres-primary db_cluster=orders-payments active_connections=44 idle_connections=28 waiting_clients=3 txn_per_s=101
- line 153 [WARN] postgres-primary: 2026-03-27T14:05:04.970Z WARN postgres-primary client=10.42.5.14 db=payments pid=48211 could not receive data from client: Connection timed out
### F4 [critical] (symptom) 24 timeout, server error status and latency deviation events recorded in api-gateway.
- Services: api-gateway; window: 2026-03-27 14:05:11.316Z to 2026-03-27 14:30:47.936Z; events: 24
- Role rationale: Starts at 14:05:11; depends on order-service, payment-service and postgres-primary, which show an upstream deviation in the same episode.
- Shift: api-gateway latency_ms baseline p95 71 -> worst 1855 (line 157)
- line 157 [WARN] api-gateway: {"ts":"2026-03-27T14:05:11.316Z","level":"WARN","service":"api-gateway","request_id":"req-84084","method":"POST","path":"/v1/orders","msg":"slow upstream res...
- line 167 [ERROR] api-gateway: {"ts":"2026-03-27T14:05:31.514Z","level":"ERROR","service":"api-gateway","request_id":"req-84172","method":"POST","path":"/v1/orders","msg":"upstream timeout...
### F5 [critical] (cause) 7 restart events recorded in postgres-primary.
- Services: postgres-primary; window: 2026-03-27 14:29:23.936Z to 2026-03-27 14:30:11.936Z; events: 7; DB categories: server_restart
- Role rationale: Starts at 14:29:23, within 30s of episode onset 14:29:23; it has no dependencies in the service graph.
- line 467 [ERROR] postgres-primary: 2026-03-27T14:29:23.936Z ERROR postgres-primary terminating connection because of crash of another server process
- line 468 [ERROR] postgres-primary: 2026-03-27T14:29:24.936Z ERROR postgres-primary all server processes terminated; reinitializing
### F6 [high] (symptom) 16 connection error, retry and queue backlog events recorded in billing-worker, inventory-service, notification-service and payment-service.
- Services: billing-worker, inventory-service, notification-service, payment-service; window: 2026-03-27 14:29:26.936Z to 2026-03-27 14:30:57.936Z; events: 16
- Role rationale: Starts at 14:29:26; depends on order-service, payment-service and postgres-primary, which show an upstream deviation in the same episode.
- Shift: inventory-service lag_ms baseline p95 36 -> worst 1633 (line 503)
- Shift: notification-service latency_ms baseline p95 122 -> worst 988 (line 505)
- line 469 [ERROR] payment-service: 2026-03-27T14:29:26.936Z ERROR payment-service request_id=req-84311 order_id=ord-51143 charge write failed error="server closed the connection unexpectedly"
- line 472 [WARN] inventory-service: 2026-03-27T14:29:32.936Z WARN inventory-service request_id=req-84311 order_id=ord-51143 reservation state unknown sku=sku-44 reason="order finalization inter...
### F7 [high] (symptom) 5 dependency unavailable, connection error and retry events recorded in order-service and payment-service.
- Services: order-service, payment-service; window: 2026-03-27 14:29:28.936Z to 2026-03-27 14:30:25.936Z; events: 5; DB categories: connection_errors
- Role rationale: Starts at 14:29:28; depends on postgres-primary, which show an upstream deviation in the same episode.
- Shift: order-service db_latency_ms baseline p95 16 -> worst 611 (line 501)
- line 470 [ERROR] order-service: 2026-03-27T14:29:28.936Z ERROR order-service request_id=req-84311 order_id=ord-51143 finalize failed error="pq: server closed the connection unexpectedly"
- line 486 [ERROR] order-service: 2026-03-27T14:29:58.936Z ERROR order-service request_id=req-84315 order_id=ord-51145 failed to persist order error="pq: the database system is starting up"
### F8 [critical] (symptom) 3 connection error and timeout events recorded in api-gateway.
- Services: api-gateway; window: 2026-03-27 14:29:30.936Z to 2026-03-27 14:30:01.936Z; events: 3
- Role rationale: Starts at 14:29:30; depends on order-service, payment-service and postgres-primary, which show an upstream deviation in the same episode.
- line 471 [ERROR] api-gateway: {"ts":"2026-03-27T14:29:30.936Z","level":"ERROR","service":"api-gateway","request_id":"req-84311","method":"POST","path":"/v1/orders","msg":"upstream error",...
- line 480 [ERROR] api-gateway: {"ts":"2026-03-27T14:29:47.936Z","level":"ERROR","service":"api-gateway","request_id":"req-84313","method":"POST","path":"/v1/orders","msg":"upstream timeout...

## Probable Cause (or Candidates)

Primary candidates:
- H1 (kind: source, confidence: medium): The pool saturation in postgres-primary appears to be a probable origin of the degradation in E1, with downstream effects possible in api-gateway. (findings: F3, F4)
  - for: Strongest deviations in postgres-primary: active_connections 100 at max_connections=100, waiting_clients up to 31 vs baseline 0.
  - for: active_connections rose 30 → 100 while txn_per_s fell 107 → 51: connections appear to be held rather than serving load.
  - for: Later deviations in api-gateway follow: F4 (24 timeout, from 14:05:11), after the deviation in postgres-primary began at 14:05:02.
  - against: Dependent services show deviations earlier: F1 at 14:04:42, F2 at 14:04:54, before postgres-primary at 14:05:02.
- H2 (kind: source, confidence: high): The restart in postgres-primary appears to be a probable origin of the degradation in E2, with downstream effects possible in api-gateway, billing-worker, inventory-service, notification-service, order-service and payment-service. (findings: F5, F6, F7, F8)
  - for: F5 records 7 restart events in postgres-primary from 14:29:23.
  - for: Later deviations in api-gateway, billing-worker, inventory-service, notification-service, order-service and payment-service follow: F6 (16 connection error, from 14:29:26), F7 (5 connection error, from 14:29:28), F8 (3 connection error, from 14:29:30), after the deviation in postgres-primary began at 14:29:23.
  - against: none identified
Alternative candidates:
- H3 (kind: episode_link, confidence: medium): The degradation in E2 may be a consequence of E1, because postgres-primary did not return to its baseline in between. (findings: F3, F5)
  - for: Between E1 and E2 the source stayed off baseline: active_connections 53–61 vs baseline 30, idle_connections 22–29 vs baseline 48.
  - for: E2 starts at 14:29:23, 1103 s after E1 ended at 14:11:00.
  - against: none identified
- H4 (kind: client_side, confidence: medium): The connection pool exhaustion in order-service and payment-service might be a client-side problem rather than a consequence of postgres-primary in E1. (findings: F1)
  - for: F1 records 51 pool saturation events in order-service and payment-service from 14:04:42, with wait_ms up to 3000.
  - for: The deviation in order-service and payment-service began at 14:04:42, before postgres-primary at 14:05:02.
  - against: Pressure on the database server is suggested by F3: 26 pool saturation at postgres-primary since 14:05:02.
- H5 (kind: load_spike, confidence: low, verdict: not_supported): A surge of incoming load at api-gateway might have contributed to the degradation in E1. (findings: F3)
  - against: Requests at api-gateway arrive at 1.3 per minute during E1 against 4.3 per minute in the baseline window (x0.31): a load spike is not supported by the log.
- H6 (kind: pre_onset_trigger, confidence: low): Unusual events in postgres-primary shortly before the onset of E1 might have triggered the degradation. (findings: F3)
  - for: Events not seen in the rest of the baseline occur within 60s before the onset at 14:04:42: 3 x "checkpointer checkpoint_complete" in postgres-primary (14:04:31 to 14:04:40).
  - against: none identified
- H7 (kind: load_spike, confidence: low, verdict: not_supported): A surge of incoming load at api-gateway might have contributed to the degradation in E2. (findings: F5)
  - against: Requests at api-gateway arrive at 4.5 per minute during E2 against 4.3 per minute in the baseline window (x1.03): a load spike is not supported by the log.

## Impact

Failed requests: 9
Affected services:
- order-service (internal): first affected 2026-03-27 14:04:42.803Z, anomalous events 43, error events 6, failed requests 6, findings: F1, F2, F7
- payment-service (internal): first affected 2026-03-27 14:04:46.050Z, anomalous events 22, error events 3, failed requests 3, findings: F1, F2, F6, F7
- inventory-service (internal): first affected 2026-03-27 14:04:54.851Z, anomalous events 31, error events 0, failed requests 0, findings: F2, F6
- postgres-primary (db_server): first affected 2026-03-27 14:05:02.164Z, anomalous events 33, error events 12, failed requests 0, findings: F3, F5
- billing-worker (internal): first affected 2026-03-27 14:05:09.633Z, anomalous events 65, error events 0, failed requests 0, findings: F2, F6
- api-gateway (entry_point): first affected 2026-03-27 14:05:11.316Z, anomalous events 27, error events 9, failed requests 9, findings: F4, F8
- notification-service (internal): first affected 2026-03-27 14:05:34.857Z, anomalous events 27, error events 0, failed requests 0, findings: F2, F6
Propagation:
- E1: postgres-primary -> order-service via db_access (2026-03-27 14:04:42.803Z); postgres-primary -> payment-service via db_access (2026-03-27 14:04:46.050Z); order-service -> inventory-service via trace_order (2026-03-27 14:04:54.851Z); order-service -> api-gateway via peer_field (2026-03-27 14:05:11.316Z); payment-service -> api-gateway via peer_field (2026-03-27 14:05:11.316Z); order-service -> notification-service via trace_order (2026-03-27 14:05:34.857Z)
- E2: postgres-primary -> payment-service via db_access (2026-03-27 14:29:26.936Z); postgres-primary -> order-service via db_access (2026-03-27 14:29:28.936Z); order-service -> api-gateway via peer_field (2026-03-27 14:29:30.936Z); payment-service -> api-gateway via peer_field (2026-03-27 14:29:30.936Z); order-service -> inventory-service via trace_order (2026-03-27 14:29:32.936Z); order-service -> notification-service via trace_order (2026-03-27 14:29:34.936Z)

## Uncertainty

- The logs do not name the cause explicitly; the hypotheses are inferred from timing and dependencies and may be wrong. (related: H1, H2)
- The logs do not include query plans or statement statistics, so which queries became slow cannot be determined. (related: F1, H4)
- The logs contain no query-level records, so what held the client pool connections (long transactions, locks or slow queries) cannot be determined. (related: F1, H4)
- The logs contain no query-level records, so what held the database connections (long transactions, locks or slow queries) cannot be determined. (related: H1, F3, H3, H5, H6)
- The logs do not show whether connection failures came from the network path, the database itself or client-side timeouts. (related: F1, H1, H2, F3, H3, H4, H5, H6, F7)
- The logs do not show which transactions were aborted when connections dropped or whether their work was retried. (related: H1, F3, H3, H5, H6)
- The logs show that the database process was killed but not who sent the signal (the OOM killer or an operator). (related: H2, H3, F5, H7)
- The logs do not include traffic metrics upstream of the entry point, so whether incoming load changed cannot be fully determined. (related: H5, H7)
- Recovery of E2 is observed, but the observation window after it is short. (related: E2)

## Next Steps

- N1 (priority 1): Check the kernel log (dmesg/journalctl) for OOM killer events and the DB host memory usage and container memory limits around the crash time. Rationale: Database server crash / restart appears in postgres-primary (7 events) and bears on the leading hypothesis. (related: H2, H3, F5, H7)
- N2 (priority 2): Check the PostgreSQL server log around the crash for the process that was killed and verify crash recovery completed cleanly. Rationale: Database server crash / restart appears in postgres-primary (7 events) and bears on the leading hypothesis. (related: H2, H3, F5, H7)
- N3 (priority 3): Check network path between services and the DB (packet loss, TCP resets, load balancer/NAT idle timeouts). Rationale: Database connection errors appears in order-service, payment-service, postgres-primary (17 events) and bears on the leading hypothesis. (related: F1, H1, H2, F3, H3, H4, H5, H6, F7)
- N4 (priority 4): Check PostgreSQL server log for disconnections and tcp_keepalives settings. Rationale: Database connection errors appears in order-service, payment-service, postgres-primary (17 events) and bears on the leading hypothesis. (related: F1, H1, H2, F3, H3, H4, H5, H6, F7)
- N5 (priority 5): Check pg_stat_activity grouped by state/application_name and compare total with max_connections. Rationale: Database server connection saturation appears in postgres-primary (15 events) and bears on the leading hypothesis. (related: H1, F3, H3, H5, H6)
- N6 (priority 6): Check whether a connection pooler (e.g. PgBouncer) is in place and its queue/wait metrics. Rationale: Database server connection saturation appears in postgres-primary (15 events) and bears on the leading hypothesis. (related: H1, F3, H3, H5, H6)
- N7 (priority 7): Check application logs and pg_stat_activity for transactions left open when client connections dropped, and whether the clients retried them. Rationale: Connections dropped with open transactions appears in postgres-primary (11 events) and bears on the leading hypothesis. (related: H1, F3, H3, H5, H6)

## DB Signals

- Elevated database latency: services order-service; 30 events; 2026-03-27 14:06:06.446Z to 2026-03-27 14:30:55.936Z; findings F1
- Client connection pool exhaustion: services order-service, payment-service; 16 events; 2026-03-27 14:04:42.803Z to 2026-03-27 14:10:43.136Z; findings F1
- Database server connection saturation: services postgres-primary; 15 events; 2026-03-27 14:05:02.164Z to 2026-03-27 14:20:14.936Z; findings F3
- Database connection errors: services order-service, payment-service, postgres-primary; 17 events; 2026-03-27 14:05:04.970Z to 2026-03-27 14:29:59.936Z; findings F1, F3, F7
- Connections dropped with open transactions: services postgres-primary; 11 events; 2026-03-27 14:05:04.970Z to 2026-03-27 14:08:23.934Z; findings F3
- Database server crash / restart: services postgres-primary; 7 events; 2026-03-27 14:29:23.936Z to 2026-03-27 14:30:11.936Z; findings F5

## Timeline

Log window: 2026-03-27 14:00:02.968Z to 2026-03-27 14:31:24.936Z
Baseline window: 2026-03-27 14:00:02.968Z to 2026-03-27 14:04:40.565Z (pre-incident period); residual anomalies outside episodes: 1
- E1: onset 2026-03-27 14:04:42.803Z, peak 2026-03-27 14:06:00.000Z to 2026-03-27 14:07:00.000Z (32 anomalous, 8 errors), last anomaly 2026-03-27 14:25:33.936Z, recovered at 2026-03-27 14:11:00.651Z (observed); 210 anomalous events, 20 error events
- E2: onset 2026-03-27 14:29:23.936Z, peak 2026-03-27 14:29:23.936Z to 2026-03-27 14:30:00.000Z (17 anomalous, 9 errors), last anomaly 2026-03-27 14:30:57.936Z, recovered at 2026-03-27 14:30:17.936Z (observed_short_window), observation window is short; 37 anomalous events, 10 error events

## Severity Scale

- critical: User-facing requests fail (5xx/timeouts at an entry point) or a data store is unavailable (crash, restart, connections refused).
- high: Errors inside a service or exhausted shared resources (connection pool, DB connections) that cause failed or aborted operations.
- medium: Degradation without outright failure: latency well above baseline, retries that eventually succeed, growing queues/backlogs.
- low: Isolated warnings or minor deviations with no visible effect on requests.

Detailed database signals, baseline comparison, request traces and input statistics: see report-appendix.md.
