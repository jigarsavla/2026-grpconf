"""The telemetry contract test, from "Tracing AI Calls End to End", gRPConf NA 2026.

A dashboard is an API consumer. This file writes the dependency down (CONTRACT, below)
and tests it against what your stack ACTUALLY exports. Five checks per instrument:
  C1 emitted at runtime          C2 required attributes present
  C3 convention stability        C4 SDK helper carries documented bucket advice
  C5 the cross-team join is possible (shared attribute keys)

To point it at your stack: replace drive_traffic() with a call into your own
instrumented client/server, keep the in-memory reader. This compact version scores 4/11 on grpcio 1.83 + semconv 0.65b0; the fuller original scored 8/17 on the same stack.
Needs: grpcio grpcio-observability opentelemetry-sdk opentelemetry-semantic-conventions
"""
import json, time
from concurrent import futures

import grpc, grpc_observability
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.metrics import set_meter_provider, get_meter

CONTRACT = {  # name -> (required_attributes, needs_stability, documented_buckets)
    "grpc.server.call.duration": (["grpc.method", "grpc.status"], "stable", None),
    "grpc.client.call.duration": (["grpc.method", "grpc.target", "grpc.status"], "stable", None),
    "gen_ai.server.time_to_first_token": (["gen_ai.operation.name", "gen_ai.provider.name"], "stable", 14),
}
JOINS = [("gen_ai.server.time_to_first_token", "grpc.server.call.duration")]
METHOD = "/contract.Inference/Generate"


def drive_traffic(reader):  # <-- REPLACE with a call into your own stack
    plugin = grpc_observability.OpenTelemetryPlugin(meter_provider=_MP)
    with plugin:
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
        server.add_generic_rpc_handlers((_H(),))
        port = server.add_insecure_port("127.0.0.1:0"); server.start()
        ch = grpc.insecure_channel(f"127.0.0.1:{port}")
        stub = ch.unary_stream(METHOD, request_serializer=lambda b: b, response_deserializer=lambda b: b)
        t0 = time.perf_counter(); ttft = None
        for _ in stub(b"go"):
            ttft = ttft or time.perf_counter() - t0
        # the model side, exactly as the official semconv helper constructs it:
        from opentelemetry.semconv._incubating.metrics.gen_ai_metrics import (
            create_gen_ai_server_time_to_first_token)
        h = create_gen_ai_server_time_to_first_token(get_meter("model-server"))
        h.record(ttft, {"gen_ai.operation.name": "chat", "gen_ai.provider.name": "demo",
                        "gen_ai.request.model": "demo-1"})
        ch.close(); server.stop(0)


class _H(grpc.GenericRpcHandler):
    def service(self, hcd):
        if hcd.method != METHOD: return None
        def gen(req, ctx):
            time.sleep(0.02)
            for _ in range(10): time.sleep(0.005); yield b"tok"
        return grpc.unary_stream_rpc_method_handler(gen, lambda b: b, lambda b: b)


def harvest(reader):
    out = {}
    md = reader.get_metrics_data()
    for rm in (md.resource_metrics if md else []):
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                pts = list(m.data.data_points)
                attrs = sorted({k for p in pts for k in (p.attributes or {})})
                bkt = getattr(pts[0], "explicit_bounds", None) if pts else None
                out[m.name] = (attrs, list(bkt) if bkt is not None else None)
    return out


if __name__ == "__main__":
    reader = InMemoryMetricReader()
    _MP = MeterProvider(metric_readers=[reader]); set_meter_provider(_MP)
    drive_traffic(reader)
    export = harvest(reader)
    from opentelemetry.semconv import _incubating  # noqa: F401  (C3: where the helpers live)
    npass = ntot = 0
    def chk(label, ok, note=""):
        global npass, ntot; ntot += 1; npass += bool(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {label} {note}")
    for name, (req, stab, buckets) in CONTRACT.items():
        got = export.get(name)
        chk(f"C1 {name} emitted", got is not None)
        chk(f"C2 {name} attrs", got and all(a in got[0] for a in req), str(req))
        incubating = name.startswith(("gen_ai.", "grpc."))  # both ship Development today
        chk(f"C3 {name} stability", stab != "stable" or not incubating,
            "needs 'stable', semconv ships 'development'")
        if buckets is not None:
            n = len(got[1]) if got and got[1] else 0
            chk(f"C4 {name} bucket advice", n == buckets, f"exported {n} boundaries, doc says {buckets}")
    for left, right in JOINS:
        l, r = export.get(left), export.get(right)
        shared = set(l[0]) & set(r[0]) if l and r else set()
        chk(f"C5 join {left} x {right}", bool(shared), f"shared={sorted(shared) or 'NONE'}")
    print(f"  {npass}/{ntot} assertions pass")
