# The telemetry contract test

From *Tracing AI Calls End to End: What AI inference teaches us about gRPC observability*,
gRPConf North America 2026.

A dashboard is a consumer of an API: it depends on specific metric names, attribute keys,
and an implicit stability promise. This test writes that dependency down and runs it
against what your stack actually exports.

```bash
pip install grpcio grpcio-observability opentelemetry-sdk opentelemetry-semantic-conventions
python3 telemetry_contract_check.py
```

To point it at your own stack, replace `drive_traffic()` with a call into your own
instrumented client/server and edit `CONTRACT` to name the instruments your dashboards
actually depend on.

On grpcio(-observability) 1.83 + opentelemetry-semantic-conventions 0.65b0 this compact
contract scores **4/11** (the fuller original scored 8/17 on the same stack). What fails,
verbatim:

- `grpc.client.call.duration` is documented as "stable, on by default" on grpc.io and is **never
  emitted** by the Python build.
- `gen_ai.server.time_to_first_token`: the official semconv helper passes no bucket
  advisory, so it exports the SDK's 15 default boundaries (top bucket 10,000 ms) instead
  of the 14 the convention's own prose documents. A true ~14 ms p50 reports as ~2,500 ms.
- the join: `gen_ai.server.*` and `grpc.*` metrics share **no attribute key**. There is
  nothing to `group_by` across the model side and the transport side.
- stability: every convention involved ships Development status. Per OTel's versioning
  spec, "Long-term dependencies SHOULD NOT be taken against signals in Development."

None of these error at runtime. That is the point of testing the contract in CI.
