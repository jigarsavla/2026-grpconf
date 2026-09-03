"""
EXP C -- The telemetry contract test.

Treat the dashboard's telemetry dependencies as an API contract (contract.yaml)
and check it in CI, the way you would check any other dependency:
  C1  does the instrument actually get emitted by the real library?
  C2  are the attributes the dashboard slices by actually present?
  C3  is the convention it belongs to stable enough to depend on?
  C4  does the helper the SDK ships carry the bucket advice the convention
      documents?
  C5  is the join the SLO assumes actually possible?
"""
import os, sys, time, yaml, importlib, inspect
import grpc, common
import inference_pb2 as pb, inference_pb2_grpc as pbg
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from grpc_observability import OpenTelemetryPlugin
import opentelemetry.semconv as semconv

def hdr(t): print("\n" + "="*78 + f"\n{t}\n" + "="*78, flush=True)
RES = {True: "PASS", False: "FAIL"}

def semconv_stability(instrument):
    """Machine-readable stability: stable conventions live in
    opentelemetry/semconv/metrics/, Development ones in _incubating/metrics/."""
    root = os.path.dirname(semconv.__file__)
    fam = instrument.split(".")[0]
    fam = {"gen_ai": "gen_ai", "grpc": "rpc", "rpc": "rpc"}.get(fam, fam)
    stable = os.path.join(root, "metrics", f"{fam}_metrics.py")
    incub  = os.path.join(root, "_incubating", "metrics", f"{fam}_metrics.py")
    if os.path.exists(stable): return "stable"
    if os.path.exists(incub):  return "development"
    return "absent"

def main():
    contract = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "contract.yaml")))

    # ---- run real traffic through gRPC's own plugin + a model-side meter ----
    r_grpc = InMemoryMetricReader(); mp_grpc = MeterProvider(metric_readers=[r_grpc])
    r_model = InMemoryMetricReader(); mp_model = MeterProvider(metric_readers=[r_model])
    plugin = OpenTelemetryPlugin(meter_provider=mp_grpc); plugin.register_global()

    # model side: use the constants and factory functions the SDK actually ships
    from opentelemetry.semconv._incubating.metrics import gen_ai_metrics as gm
    m_model = mp_model.get_meter("model-server")
    h_ttft = gm.create_gen_ai_server_time_to_first_token(m_model)
    h_tpot = gm.create_gen_ai_server_time_per_output_token(m_model)

    srv, sv, port = common.start_server()
    ch = grpc.insecure_channel(f"127.0.0.1:{port}"); stub = pbg.InferenceStub(ch)
    MODEL_ATTRS = {"gen_ai.operation.name": "chat", "gen_ai.provider.name": "vllm"}
    for i in range(4):
        rid=f"c-{i}"
        common.ClientObserver().run(stub, common.mk_req("decode_heavy"), rid)
        led = stub.GetLedger(pb.LedgerRequest(request_id=rid))
        h_ttft.record(led.ttft_ms/1000, MODEL_ATTRS)
        h_tpot.record(led.tpot_ms/1000, MODEL_ATTRS)
    time.sleep(2.0)

    emitted = {}
    for reader in (r_grpc, r_model):
        d = reader.get_metrics_data()
        if not d: continue
        for rm in d.resource_metrics:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    keys=set(); nbuckets=None
                    for dp in m.data.data_points:
                        keys |= set(dp.attributes.keys())
                        nb = getattr(dp, "explicit_bounds", None)
                        if nb is not None: nbuckets = len(nb)
                    e = emitted.setdefault(m.name, {"attrs": set(), "buckets": None})
                    e["attrs"] |= keys
                    if nbuckets is not None: e["buckets"] = nbuckets
    plugin.deregister_global(); srv.stop(0)

    hdr("C1-C4. Contract assertions against the REAL libraries")
    total=0; failed=0
    for dep in contract["depends_on"]:
        name = dep["instrument"]
        print(f"\n  {name}   (emitted_by: {dep['emitted_by']})")
        # C1 emitted?
        present = name in emitted
        total+=1; failed += not present
        print(f"    C1 emitted at runtime                     [{RES[present]}]"
              + ("" if present else "   <- never appears in the export"))
        # C2 attributes
        if present:
            missing = set(dep["required_attributes"]) - emitted[name]["attrs"]
            ok = not missing
            total+=1; failed += not ok
            print(f"    C2 required attributes present            [{RES[ok]}]"
                  + (f"   missing {sorted(missing)}" if missing else
                     f"   {sorted(emitted[name]['attrs'])}"))
        # C3 stability
        st = semconv_stability(name)
        ok = (st == dep["needs_stability"])
        total+=1; failed += not ok
        print(f"    C3 convention stability                   [{RES[ok]}]"
              f"   needs '{dep['needs_stability']}', semconv ships '{st}'")
        # C4 bucket advice
        if dep.get("needs_bucket_advice"):
            fn = getattr(gm, "create_" + name.replace(".", "_"), None)
            src = inspect.getsource(fn) if fn else ""
            ok = "explicit_bucket_boundaries_advisory" in src
            total+=1; failed += not ok
            print(f"    C4 SDK helper carries bucket advice       [{RES[ok]}]")
            if not ok and fn:
                print("       helper source:")
                for ln in src.strip().splitlines()[-4:]:
                    print("         " + ln.strip())
            if present and emitted[name]["buckets"] is not None:
                print(f"       -> exported with {emitted[name]['buckets']} bucket boundaries"
                      f" (SDK default is 15)")

    hdr("C5. Is the join the SLO assumes actually possible?")
    for j in contract["joins"]:
        l, r = j["left"], j["right"]
        la = emitted.get(l, {}).get("attrs", set())
        ra = emitted.get(r, {}).get("attrs", set())
        shared = la & ra
        ok = bool(shared)
        total+=1; failed += not ok
        print(f"\n  join '{j['name']}'                          [{RES[ok]}]")
        print(f"    {l:38s} {sorted(la)}")
        print(f"    {r:38s} {sorted(ra)}")
        print(f"    shared dimensions: {sorted(shared) if shared else 'NONE'}")

    hdr("CONTRACT RESULT")
    print(f"  {total-failed}/{total} assertions pass, {failed} FAIL.")
    print("  Every failure is a real property of released software, not of this rig.")
    print("  A dashboard is a consumer of a telemetry API. Nobody writes down the")
    print("  contract, so nobody notices when it is not met.")

if __name__ == "__main__":
    main()
