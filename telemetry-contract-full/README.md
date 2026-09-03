# The telemetry contract test, full version

The 17-check version of the contract test. The compact version at
[`../telemetry-contract-test/`](../telemetry-contract-test/) is the one on the
handout QR and scores 4 of 11. This one scores **8 of 17** on the same stack
(grpcio 1.83, opentelemetry-semantic-conventions 0.65b0).

```bash
pip install grpcio grpcio-observability opentelemetry-sdk opentelemetry-semantic-conventions pyyaml
python3 exp_c_contract.py     # the contract runner, about 20 seconds
./run.sh                      # all four experiments, about 7 minutes
```

`contract.yaml` is the dependency written down: which instruments the dashboards
rely on, which attributes they slice by, what stability they need, and which joins
they assume. `exp_c_contract.py` drives real traffic through gRPC's own
OpenTelemetry plugin and the official GenAI semconv helpers, then checks every line.

The other three experiments are the evidence behind the "one trace, two audiences"
part of the talk: `exp_a` injects five faults and records whose dashboard moves,
`exp_b` shows three correct and different answers to "what was the time to first
token" on one request, and `exp_d` counts how many metric conventions are stable
enough to build an SLO on (two of twenty-three, and they are `db` and `http`).
