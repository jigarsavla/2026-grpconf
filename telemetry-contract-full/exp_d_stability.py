"""
EXP D -- Is there a contract at all? Stability, schema_url, and rename drift.

D1  Census of the installed opentelemetry-semantic-conventions package:
    how many metric/attribute families are STABLE vs DEVELOPMENT?
D2  Who declares grpc.* stable, and does the consumer have any way to know?
D3  schema_url: the migration mechanism requires it. Does anything set it?
D4  The rename drill: what a dashboard sees when a Development convention
    exercises the right the spec grants it.
"""
import os, re, time, sys, inspect
import grpc, common
import inference_pb2 as pb, inference_pb2_grpc as pbg
import opentelemetry.semconv as semconv
from opentelemetry.semconv.schemas import Schemas
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from grpc_observability import OpenTelemetryPlugin

def hdr(t): print("\n" + "="*78 + f"\n{t}\n" + "="*78, flush=True)

ROOT = os.path.dirname(semconv.__file__)

def census(kind):
    stable = sorted(f[:-3] for f in os.listdir(os.path.join(ROOT, kind))
                    if f.endswith("_%s.py" % kind[:-1]) or (f.endswith(".py") and not f.startswith("__")))
    incub  = sorted(f[:-3] for f in os.listdir(os.path.join(ROOT, "_incubating", kind))
                    if f.endswith(".py") and not f.startswith("__"))
    return stable, incub

def main():
    import opentelemetry.semconv.version as v
    hdr("D1. Stability census of the installed semantic-convention package")
    try:
        ver = v.__version__
    except Exception:
        ver = "unknown"
    print(f"\n  opentelemetry-semantic-conventions {ver}")
    for kind in ("metrics", "attributes"):
        st, inc = census(kind)
        sset, iset = set(st), set(inc)
        only_dev = sorted(iset - sset)
        print(f"\n  {kind.upper()}  (a family may have BOTH a stable and an incubating module)")
        print(f"    families with ANY stable module   ({len(sset):2d}): {sorted(sset)}")
        print(f"    families with an incubating module({len(iset):2d})")
        print(f"    families that exist ONLY as Development ({len(only_dev):2d}):")
        print(f"      {', '.join(only_dev)}")
        for fam in ("gen_ai", "rpc"):
            where = "STABLE" if any(s.startswith(fam) for s in st) else \
                    ("DEVELOPMENT" if any(i.startswith(fam) for i in inc) else "absent")
            print(f"    -> {fam+'.*':10s} is {where}")
    print("\n  Both personas' latency conventions ship in _incubating. The OTel")
    print("  versioning spec on Development signals: \"Long-term dependencies")
    print("  SHOULD NOT be taken against signals in Development.\" An SLO is a")
    print("  long-term dependency. Everyone has one anyway.")

    # ------------------------------------------------------------------ D2
    hdr("D2. Two authorities, two answers, for the SAME metric name")
    print("""
  grpc.server.call.duration
    gRFC A66            : defined here, and A66 states it OVERRIDES OTel's
                          general RPC conventions for gRPC
    grpc.io docs        : listed under "Server (stable, on by default)"
    OTel semconv package: ships rpc_metrics under _incubating/  -> DEVELOPMENT
    OTLP on the wire    : carries no stability field at all (see D3)

  gen_ai.server.time_to_first_token
    semconv-genai docs  : Development
    OTel semconv package: _incubating/  -> DEVELOPMENT
    consistent -- and consistently unusable as an SLO by the spec's own rule.

  A consumer holding a dashboard has no programmatic way to ask "may I depend
  on this name?" The answer lives in three documents and none of them travels
  with the data.""")

    # ------------------------------------------------------------------ D3
    hdr("D3. schema_url -- the migration mechanism's precondition")
    print(f"\n  The package knows about {len(list(Schemas))} schema versions; newest:")
    print(f"    {list(Schemas)[-1].value}")

    r_grpc = InMemoryMetricReader(); mp_grpc = MeterProvider(metric_readers=[r_grpc])
    r_app  = InMemoryMetricReader(); mp_app  = MeterProvider(metric_readers=[r_app])
    plugin = OpenTelemetryPlugin(meter_provider=mp_grpc); plugin.register_global()

    m_plain = mp_app.get_meter("app-no-schema")
    m_sch   = mp_app.get_meter("app-with-schema", schema_url=list(Schemas)[-1].value)
    h_plain = m_plain.create_histogram("gen_ai.server.time_to_first_token", unit="s")
    h_sch   = m_sch.create_histogram("gen_ai.server.time_to_first_token", unit="s")

    srv, sv, port = common.start_server()
    ch = grpc.insecure_channel(f"127.0.0.1:{port}"); stub = pbg.InferenceStub(ch)
    for i in range(3):
        rid=f"d-{i}"
        common.ClientObserver().run(stub, common.mk_req("decode_heavy"), rid)
        led = stub.GetLedger(pb.LedgerRequest(request_id=rid))
        h_plain.record(led.ttft_ms/1000, {"gen_ai.provider.name":"vllm"})
        h_sch.record(led.ttft_ms/1000,   {"gen_ai.provider.name":"vllm"})
    time.sleep(2.0)

    print(f"\n  {'scope':26s} {'schema_url':46s} {'metrics'}")
    print("  " + "-"*82)
    rows=[]
    for label, reader in (("gRPC A66 plugin", r_grpc), ("application", r_app)):
        d = reader.get_metrics_data()
        if not d: continue
        for rm in d.resource_metrics:
            for sm in rm.scope_metrics:
                su = sm.schema_url or ""
                rows.append((sm.scope.name, su, len(sm.metrics)))
                print(f"  {sm.scope.name[:25]:26s} {(su or '(none)'):46s} {len(sm.metrics)}")
    plugin.deregister_global(); srv.stop(0)
    noschema = [r for r in rows if not r[1]]
    print("  " + "-"*82)
    print(f"  {len(noschema)} of {len(rows)} emitting scopes carry NO schema_url.")
    print("""
  A schema file can express rename_metrics / rename_attributes / split, and a
  consumer can only apply one if the data says which schema version it was
  produced against. gRPC's own OTel plugin emits none. And per the OTel
  telemetry-stability spec there is currently "a moratorium on relying on
  schema transformations for telemetry stability" -- so the one mechanism
  designed to let a convention change safely is both unset in practice and
  suspended in principle.""")

    # ------------------------------------------------------------------ D4
    hdr("D4. The rename drill -- what a dashboard sees")
    OLD = "gen_ai.client.operation.time_to_first_token"     # plausible earlier name
    NEW = "gen_ai.client.operation.time_to_first_chunk"     # what ships today
    print(f"""
  A Development convention is permitted to rename. Today the client-side
  series is named for CHUNKS and the server-side series for TOKENS:
      client : gen_ai.client.operation.time_to_first_chunk
      server : gen_ai.server.time_to_first_token
  (NB: I could find no PR evidence that the client name was ever 'token' --
   presented here as a RENAME DRILL, not as rename history.)

  A PromQL/OTel dashboard panel is a string:
      histogram_quantile(0.5, rate({{__name__="{OLD}_bucket"}}[5m]))

  After a rename, with no schema_url on the data:""")
    series = {NEW: 3}
    q = series.get(OLD, 0)
    print(f"    series matching the panel's name : {q}")
    print(f"    series actually being produced   : {series[NEW]} (under {NEW})")
    print("""    result rendered                  : "No data"
    alert on that panel               : does not fire; it also does not error

  This is the same failure mode as the silent propagation break and the
  dropped_events_count: the system is not broken, it is empty, and empty looks
  like healthy.""")

if __name__ == "__main__":
    main()
