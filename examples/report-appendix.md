# Incident Report Appendix: it_task_log (2).log

## DB Signals (detailed)

- Elevated database latency [db_latency]
  - Services: order-service
  - Window: 2026-03-27 14:06:06.446Z to 2026-03-27 14:30:55.936Z
  - Events: 30
  - Findings: F1
  - Hypotheses: H4
  - db_latency_ms (ms): n=14, min 77, p50 141, max 611, baseline p95 16
  - latency_ms (ms): n=16, min 521, p50 905, max 2144, baseline p95 89
  - line 186 [INFO] order-service: 2026-03-27T14:06:06.446Z INFO order-service request_id=req-84183 order_id=ord-51123 stage=validate items=1 db_latency_ms=77 cache_hit=false
  - line 298 [WARN] order-service: 2026-03-27T14:08:37.640Z WARN order-service request_id=req-84211 order_id=ord-51127 stage=validate items=1 db_latency_ms=293 cache_hit=true
  - line 301 [INFO] order-service: 2026-03-27T14:08:42.939Z INFO order-service request_id=req-84211 order_id=ord-51127 state=confirmed total_cents=2799 latency_ms=846
- Client connection pool exhaustion [pool_exhaustion]
  - Services: order-service, payment-service
  - Window: 2026-03-27 14:04:42.803Z to 2026-03-27 14:10:43.136Z
  - Events: 16
  - Findings: F1
  - Hypotheses: H4
  - duration_ms (ms): n=1, min 641, p50 641, max 641
  - pool_idle (count): n=7, min 0, p50 0, max 1
  - pool_in_use (count): n=7, min 29, p50 32, max 32
  - wait_ms (ms): n=7, min 418, p50 1822, max 2894
  - line 143 [WARN] order-service: 2026-03-27T14:04:42.803Z WARN order-service db acquire connection slow wait_ms=418 pool_in_use=29 pool_idle=1
  - line 144 [WARN] payment-service: 2026-03-27T14:04:46.050Z WARN payment-service db query latency high op=select_payment_profile duration_ms=641 pool_in_use=27 pool_idle=2
  - line 166 [WARN] payment-service: 2026-03-27T14:05:29.231Z WARN payment-service request_id=req-84172 order_id=ord-51121 db timeout acquiring connection wait_ms=3000 pool_in_use=32 pool_idle=0
- Database server connection saturation [server_saturation]
  - Services: postgres-primary
  - Window: 2026-03-27 14:05:02.164Z to 2026-03-27 14:20:14.936Z
  - Events: 15
  - Findings: F3
  - Hypotheses: H1, H3, H5, H6
  - active_connections (count): n=14, min 44, p50 73, max 100, baseline p95 30
  - idle_connections (count): n=12, min 0, p50 10, max 28, baseline p95 55
  - txn_per_s (per_s): n=12, min 51, p50 79, max 101, baseline p95 121
  - waiting_clients (count): n=14, min 0, p50 9, max 31, baseline p95 0
  - line 152 [WARN] postgres-primary: 2026-03-27T14:05:02.164Z WARN postgres-primary db_cluster=orders-payments active_connections=44 idle_connections=28 waiting_clients=3 txn_per_s=101
  - line 177 [WARN] postgres-primary: 2026-03-27T14:05:49.974Z WARN postgres-primary db_cluster=orders-payments active_connections=71 idle_connections=4 waiting_clients=11 txn_per_s=88
  - line 434 [INFO] postgres-primary: 2026-03-27T14:20:14.936Z INFO postgres-primary db_cluster=orders-payments active_connections=60 idle_connections=23 waiting_clients=0 txn_per_s=94
- Database connection errors [connection_errors]
  - Services: order-service, payment-service, postgres-primary
  - Window: 2026-03-27 14:05:04.970Z to 2026-03-27 14:29:59.936Z
  - Events: 17
  - Findings: F1, F3, F7
  - Hypotheses: H1, H2, H3, H4, H5, H6
  - line 153 [WARN] postgres-primary: 2026-03-27T14:05:04.970Z WARN postgres-primary client=10.42.5.14 db=payments pid=48211 could not receive data from client: Connection timed out
  - line 173 [ERROR] order-service: 2026-03-27T14:05:42.391Z ERROR order-service request_id=req-84175 order_id=ord-51122 failed to persist order error="pq: connection reset by peer"
  - line 470 [ERROR] order-service: 2026-03-27T14:29:28.936Z ERROR order-service request_id=req-84311 order_id=ord-51143 finalize failed error="pq: server closed the connection unexpectedly"
- Connections dropped with open transactions [aborted_transactions]
  - Services: postgres-primary
  - Window: 2026-03-27 14:05:04.970Z to 2026-03-27 14:08:23.934Z
  - Events: 11
  - Findings: F3
  - Hypotheses: H1, H3, H5, H6
  - line 153 [WARN] postgres-primary: 2026-03-27T14:05:04.970Z WARN postgres-primary client=10.42.5.14 db=payments pid=48211 could not receive data from client: Connection timed out
  - line 178 [ERROR] postgres-primary: 2026-03-27T14:05:52.235Z ERROR postgres-primary client=10.42.7.18 db=orders pid=48289 could not receive data from client: Connection reset by peer
  - line 179 [ERROR] postgres-primary: 2026-03-27T14:05:54.311Z ERROR postgres-primary client=10.42.5.14 db=payments pid=48291 unexpected EOF on client connection with an open transaction
- Database server crash / restart [server_restart]
  - Services: postgres-primary
  - Window: 2026-03-27 14:29:23.936Z to 2026-03-27 14:30:11.936Z
  - Events: 7
  - Findings: F5
  - Hypotheses: H2, H3, H7
  - line 467 [ERROR] postgres-primary: 2026-03-27T14:29:23.936Z ERROR postgres-primary terminating connection because of crash of another server process
  - line 468 [ERROR] postgres-primary: 2026-03-27T14:29:24.936Z ERROR postgres-primary all server processes terminated; reinitializing
  - line 475 [WARN] postgres-primary: 2026-03-27T14:29:38.936Z WARN postgres-primary systemd[1]: postgresql.service: Main process exited, code=killed, status=9/KILL

## Baseline vs Incident

| Service | Metric | Unit | Baseline | Incident | p95 ratio |
| --- | --- | --- | --- | --- | --- |
| api-gateway | latency_ms | ms | n=16 p50=54 p95=71 max=71 | n=18 p50=711 p95=1855 max=1855 | 26.13 |
| billing-worker | duration_ms | ms | n=9 p50=194 p95=240 max=240 | n=18 p50=1988 p95=2440 max=2440 | 10.17 |
| billing-worker | last_duration_ms | ms | n/a | n=4 p50=1894 p95=2254 max=2254 | n/a |
| billing-worker | oldest_job_age_ms | ms | n/a | n=26 p50=15760 p95=26040 max=27600 | n/a |
| billing-worker | queue_depth | count | n=9 p50=1 p95=2 max=2 | n=72 p50=17 p95=42 max=45 | 21 |
| billing-worker | success_rate_5m | ratio | n/a | n=3 p50=0.83 p95=0.91 max=0.91 | n/a |
| ingress-controller | latency_ms | ms | n=5 p50=6 p95=9 max=9 | n=16 p50=5 p95=7 max=7 | 0.78 |
| inventory-service | cache_version_lag | count | n/a | n=4 p50=1 p95=1 max=1 | n/a |
| inventory-service | event_lag_ms | ms | n/a | n=4 p50=7165 p95=9067 max=9067 | n/a |
| inventory-service | lag_ms | ms | n=16 p50=25 p95=36 max=36 | n=17 p50=739 p95=1633 max=1633 | 45.36 |
| inventory-service | max_event_lag_ms | ms | n/a | n=2 p50=822 p95=2211 max=2211 | n/a |
| inventory-service | pending_events | count | n/a | n=2 p50=7 p95=18 max=18 | n/a |
| inventory-service | queue_age_ms | ms | n/a | n=4 p50=2741 p95=5021 max=5021 | n/a |
| kubelet | latency_ms | ms | n=8 p50=3 p95=3 max=3 | n=11 p50=2 p95=4 max=4 | 1.33 |
| notification-service | latency_ms | ms | n=16 p50=81 p95=122 max=122 | n=19 p50=412 p95=1422 max=1422 | 11.66 |
| notification-service | oldest_message_age_ms | ms | n/a | n=2 p50=944 p95=3034 max=3034 | n/a |
| notification-service | pending_messages | count | n/a | n=6 p50=14 p95=24 max=24 | n/a |
| notification-service | queue_delay_ms | ms | n/a | n=10 p50=3874 p95=7738 max=7738 | n/a |
| order-service | db_latency_ms | ms | n=16 p50=11 p95=16 max=16 | n=20 p50=120 p95=293 max=611 | 18.31 |
| order-service | latency_ms | ms | n=16 p50=65 p95=89 max=89 | n=17 p50=905 p95=2144 max=2144 | 24.09 |
| order-service | pool_idle | count | n/a | n=7 p50=0 p95=1 max=1 | n/a |
| order-service | pool_in_use | count | n/a | n=7 p50=32 p95=32 max=32 | n/a |
| order-service | wait_ms | ms | n/a | n=7 p50=1822 p95=2894 max=2894 | n/a |
| payment-service | db_wait_ms | ms | n/a | n=4 p50=744 p95=1408 max=1408 | n/a |
| payment-service | duration_ms | ms | n/a | n=1 p50=641 p95=641 max=641 | n/a |
| payment-service | latency_ms | ms | n=16 p50=145 p95=169 max=169 | n=14 p50=439 p95=1189 max=1189 | 7.04 |
| payment-service | pool_idle | count | n/a | n=8 p50=0 p95=3 max=3 | n/a |
| payment-service | pool_in_use | count | n/a | n=8 p50=30 p95=32 max=32 | n/a |
| payment-service | wait_ms | ms | n/a | n=7 p50=2411 p95=3000 max=3000 | n/a |
| postgres-primary | active_connections | count | n=6 p50=30 p95=30 max=30 | n=19 p50=71 p95=100 max=100 | 3.33 |
| postgres-primary | duration_ms | ms | n=3 p50=55 p95=61 max=61 | n=3 p50=97 p95=119 max=119 | 1.95 |
| postgres-primary | idle_connections | count | n=6 p50=48 p95=55 max=55 | n=17 p50=14 p95=40 max=40 | 0.73 |
| postgres-primary | txn_per_s | per_s | n=6 p50=107 p95=121 max=121 | n=17 p50=84 p95=101 max=101 | 0.83 |
| postgres-primary | waiting_clients | count | n=6 p50=0 p95=0 max=0 | n=19 p50=5 p95=31 max=31 | n/a |

Rows without incident values or without a change in p95 are omitted: 3 of 37 metric rows.

| Service | Baseline errors | Baseline rate | Incident errors | Incident rate |
| --- | --- | --- | --- | --- |
| api-gateway | 0/20 | 0 | 9/35 | 0.257 |
| billing-worker | 0/9 | 0 | 0/72 | 0 |
| ingress-controller | 0/5 | 0 | 0/16 | 0 |
| inventory-service | 0/16 | 0 | 0/32 | 0 |
| kubelet | 0/8 | 0 | 0/11 | 0 |
| notification-service | 0/16 | 0 | 0/34 | 0 |
| order-service | 0/32 | 0 | 6/50 | 0.12 |
| payment-service | 0/16 | 0 | 3/29 | 0.103 |
| postgres-primary | 0/9 | 0 | 12/47 | 0.255 |
| redis-cache | 0/11 | 0 | 0/25 | 0 |

## Request Traces

- Request req-84183: api-gateway -> order-service -> payment-service -> inventory-service -> notification-service (8 anomalous of 9 hops, 2026-03-27 14:06:05.081Z to 2026-03-27 14:08:09.997Z)
  - line 185 2026-03-27 14:06:05.081Z api-gateway [INFO]: {method,path,upstream}
  - line 186 2026-03-27 14:06:06.446Z order-service [INFO] anomalous: {cache_hit,db_latency_ms,items,stage}
  - line 187 2026-03-27 14:06:08.465Z payment-service [WARN] anomalous: db acquire connection slow
  - line 188 2026-03-27 14:06:08.476Z payment-service [ERROR] anomalous: java.sql.SQLTransientConnectionException: HikariPool-<n> - Connection is not available, request timed out after <n>0ms.
  - line 195 2026-03-27 14:06:10.514Z api-gateway [ERROR] anomalous: upstream timeout
  - line 197 2026-03-27 14:06:13.668Z inventory-service [WARN] anomalous: reservation event delayed
  - line 198 2026-03-27 14:06:15.383Z notification-service [WARN] anomalous: delivery deferred
  - line 281 2026-03-27 14:08:08.757Z inventory-service [WARN] anomalous: stale reservation state detected
  - line 282 2026-03-27 14:08:09.997Z notification-service [WARN] anomalous: delivery queue lag detected
- Request req-84172: api-gateway -> order-service -> payment-service -> inventory-service -> notification-service (7 anomalous of 9 hops, 2026-03-27 14:05:24.168Z to 2026-03-27 14:08:06.904Z)
  - line 163 2026-03-27 14:05:24.168Z api-gateway [INFO]: {method,path,upstream}
  - line 164 2026-03-27 14:05:25.758Z order-service [INFO]: {cache_hit,db_latency_ms,items,stage}
  - line 165 2026-03-27 14:05:27.151Z order-service [WARN] anomalous: db acquire connection slow
  - line 166 2026-03-27 14:05:29.231Z payment-service [WARN] anomalous: db timeout acquiring connection
  - line 167 2026-03-27 14:05:31.514Z api-gateway [ERROR] anomalous: upstream timeout
  - line 168 2026-03-27 14:05:33.170Z inventory-service [WARN] anomalous: reservation update delayed awaiting order finalization
  - line 169 2026-03-27 14:05:34.857Z notification-service [WARN] anomalous: delivery deferred
  - line 279 2026-03-27 14:08:05.752Z inventory-service [WARN] anomalous: stale reservation state detected
  - line 280 2026-03-27 14:08:06.904Z notification-service [WARN] anomalous: delivery queue lag detected
- Request req-84194: api-gateway -> order-service -> payment-service -> inventory-service -> notification-service (7 anomalous of 8 hops, 2026-03-27 14:06:57.889Z to 2026-03-27 14:08:12.747Z)
  - line 229 2026-03-27 14:06:57.889Z api-gateway [INFO]: {method,path,upstream}
  - line 230 2026-03-27 14:06:59.510Z order-service [WARN] anomalous: db acquire connection slow
  - line 231 2026-03-27 14:07:00.772Z payment-service [WARN] anomalous: db timeout acquiring connection
  - line 232 2026-03-27 14:07:02.106Z api-gateway [ERROR] anomalous: upstream timeout
  - line 233 2026-03-27 14:07:03.399Z inventory-service [WARN] anomalous: reservation event delayed
  - line 234 2026-03-27 14:07:04.358Z notification-service [WARN] anomalous: delivery deferred
  - line 283 2026-03-27 14:08:11.368Z inventory-service [WARN] anomalous: stale reservation state detected
  - line 284 2026-03-27 14:08:12.747Z notification-service [WARN] anomalous: delivery queue lag detected

## Input Statistics

- File: it_task_log (2).log
- SHA-256: 376c84ce0ae23fe216dea46d789a47410ebdd0ebd8bf9b4b834073f4dac2c839
- Total lines: 521
- Blank lines: 0
- Parsed lines: 521
- Not parsed lines: 0
- Dropped as noise: 262
- Anomalous lines: 248
- Continuation lines: 11
- Services: api-gateway, billing-worker, ingress-controller, inventory-service, kubelet, notification-service, order-service, payment-service, postgres-primary, redis-cache
