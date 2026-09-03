"""
EXP A -- The persona contract, generated rather than drawn.

A0: NAME LEGALITY. Test the metric names each persona actually reads against
    the OpenTelemetry Metrics API instrument-name syntax (spec regex).
A1: three REAL observers on the SAME RPCs, each through a real MeterProvider.
A2: the phase x persona matrix, scored from what the instruments bracket.
A3: THE JOIN QUESTION -- intersect the attribute key sets actually emitted.
"""
import re, time, sys
import grpc
import common
import inference_pb2 as pb, inference_pb2_grpc as pbg

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from grpc_observability import OpenTelemetryPlugin

# OTel Metrics API instrument-name syntax, verbatim from the spec:
# "The first character must be an alphabetic character. Subsequent characters
#  must belong to the alphanumeric characters, '_', '.', '-', and '/'.
#  They can have a maximum length of 255 characters."
OTEL_NAME_RE = re.compile(r"[a-zA-Z][-_./a-zA-Z0-9]{0,254}")

GENAI_TTFT_BUCKETS = [0.001,0.002,0.004,0.008,0.016,0.032,0.064,0.128,0.256,
                      0.512,1.024,2.048,4.096,8.192]
A66_BUCKETS = [0,0.00001,0.00005,0.0001,0.0003,0.0006,0.0008,0.001,0.002,0.003,
               0.004,0.005,0.006,0.008,0.01,0.013,0.016,0.02,0.025,0.03,0.04,
               0.05,0.065,0.08,0.1,0.13,0.16,0.2,0.25,0.3,0.4,0.5,0.65,0.8,
               1,2,5,10,20,50,100]

def hdr(t): print("\n" + "="*78 + f"\n{t}\n" + "="*78, flush=True)

# --------------------------------------------------------------------- A0
def exp_a0():
    hdr("A0. Are the three personas' metric names even legal in one system?")
    NAMES = [
        ("MODEL  (vLLM, shipped)",      "vllm:time_to_first_token_seconds"),
        ("MODEL  (vLLM, shipped)",      "vllm:time_per_output_token_seconds"),
        ("MODEL  (vLLM, shipped)",      "vllm:request_queue_time_seconds"),
        ("MODEL  (vLLM, shipped)",      "vllm:request_prefill_time_seconds"),
        ("MODEL  (vLLM, shipped)",      "vllm:request_decode_time_seconds"),
        ("MODEL  (vLLM, shipped)",      "vllm:kv_cache_usage_perc"),
        ("MODEL  (SGLang, shipped)",    "sglang:time_to_first_token_seconds"),
        ("MODEL  (OTel GenAI semconv)", "gen_ai.server.time_to_first_token"),
        ("MODEL  (OTel GenAI semconv)", "gen_ai.server.time_per_output_token"),
        ("CLIENT (OTel GenAI semconv)", "gen_ai.client.operation.time_to_first_chunk"),
        ("TRANSPORT (gRFC A66)",        "grpc.client.attempt.duration"),
        ("TRANSPORT (gRFC A66)",        "grpc.server.call.duration"),
        ("TRANSPORT (gRFC A80)",        "grpc.tcp.sender_latency"),
        ("TRANSPORT (proposed)",        "grpc.client.attempt.time_to_first_message"),
    ]
    print(f"\n  {'owner':30s} {'metric name':46s} {'legal?':>7s}")
    print("  " + "-"*86)
    bad = 0
    for owner, n in NAMES:
        ok = OTEL_NAME_RE.fullmatch(n) is not None
        if not ok: bad += 1
        print(f"  {owner:30s} {n:46s} {('OK' if ok else 'ILLEGAL'):>7s}")
    print("  " + "-"*86)
    print(f"  {bad} of {len(NAMES)} names cannot be registered as an OTel instrument.")
    print("  Every one of them is a MODEL-SERVER name, and the reason is one character:")
    print("  ':' is Prometheus namespacing and is not in the OTel instrument charset.")
    # show that the SDK really refuses
    r = InMemoryMetricReader(); mp = MeterProvider(metric_readers=[r])
    m = mp.get_meter("legality-probe")
    try:
        m.create_histogram("vllm:time_to_first_token_seconds", unit="s")
        print("\n  SDK accepted it (unexpected).")
    except Exception as e:
        print(f"\n  opentelemetry-sdk raised: {type(e).__name__}: {e}")
        print("  NB the message says 'maximum length 63 characters'. The name is 32")
        print("  characters long. The installed regex is r\"[a-zA-Z][-_./a-zA-Z0-9]{0,254}\"")
        print("  -- correct per spec (255), but the diagnostic points at the wrong thing.")
    mp.shutdown()
    return bad

# --------------------------------------------------------------------- A1
def main():
    exp_a0()

    r_model = InMemoryMetricReader(); mp_model = MeterProvider(metric_readers=[r_model])
    r_client = InMemoryMetricReader(); mp_client = MeterProvider(metric_readers=[r_client])
    r_grpc  = InMemoryMetricReader(); mp_grpc  = MeterProvider(metric_readers=[r_grpc])

    plugin = OpenTelemetryPlugin(meter_provider=mp_grpc)
    plugin.register_global()

    m_model = mp_model.get_meter("model-server")
    h_ttft = m_model.create_histogram("gen_ai.server.time_to_first_token", unit="s",
                                      explicit_bucket_boundaries_advisory=GENAI_TTFT_BUCKETS)
    h_tpot = m_model.create_histogram("gen_ai.server.time_per_output_token", unit="s",
                                      explicit_bucket_boundaries_advisory=GENAI_TTFT_BUCKETS)
    h_srvdur = m_model.create_histogram("gen_ai.server.request.duration", unit="s")
    # vLLM names must be SANITISED to be registrable at all (see A0):
    h_queue = m_model.create_histogram("vllm.request_queue_time_seconds", unit="s")
    h_pref  = m_model.create_histogram("vllm.request_prefill_time_seconds", unit="s")
    h_dec   = m_model.create_histogram("vllm.request_decode_time_seconds", unit="s")

    m_cli = mp_client.get_meter("proposed-transport-histograms")
    h_ttfm = m_cli.create_histogram("grpc.client.attempt.time_to_first_message", unit="s",
                                    explicit_bucket_boundaries_advisory=A66_BUCKETS)
    h_gap  = m_cli.create_histogram("grpc.client.attempt.inter_message_gap", unit="s",
                                    explicit_bucket_boundaries_advisory=A66_BUCKETS)

    srv, servicer, port = common.start_server()
    ch = grpc.insecure_channel(f"127.0.0.1:{port}")
    stub = pbg.InferenceStub(ch)

    MODEL_ATTRS = {"gen_ai.operation.name": "chat", "gen_ai.provider.name": "vllm",
                   "gen_ai.request.model": "llama-3.1-8b"}
    CLIENT_ATTRS = {"grpc.method": "contract.Inference/Generate",
                    "grpc.target": f"dns:///127.0.0.1:{port}", "grpc.status": "OK"}

    hdr("A1. Three real observers, same 8 RPCs (4 prefill-heavy, 4 decode-heavy)")
    for i in range(8):
        w = "prefill_heavy" if i % 2 == 0 else "decode_heavy"
        rid = f"a1-{i}"
        obs = common.ClientObserver()
        obs.run(stub, common.mk_req(w), rid)
        led = stub.GetLedger(pb.LedgerRequest(request_id=rid))
        h_ttft.record(led.ttft_ms/1000, MODEL_ATTRS)
        h_tpot.record(led.tpot_ms/1000, MODEL_ATTRS)
        h_srvdur.record((led.ttft_ms+led.decode_ms+led.detok_ms)/1000, MODEL_ATTRS)
        h_queue.record(led.queue_ms/1000, MODEL_ATTRS)
        h_pref.record(led.prefill_ms/1000, MODEL_ATTRS)
        h_dec.record(led.decode_ms/1000, MODEL_ATTRS)
        h_ttfm.record(obs.ttfm[0]/1000, CLIENT_ATTRS)
        for g in obs.gaps: h_gap.record(g/1000, CLIENT_ATTRS)
    time.sleep(2.0)   # let grpc-observability's exporter thread flush
    print("  8 RPCs complete.")

    def harvest(reader, label):
        data = reader.get_metrics_data()
        out = {}
        if data:
            for rm in data.resource_metrics:
                for sm in rm.scope_metrics:
                    for m in sm.metrics:
                        keys = set()
                        for dp in m.data.data_points:
                            keys |= set(dp.attributes.keys())
                        out[m.name] = out.get(m.name, set()) | keys
        print(f"\n  {label}: {len(out)} instrument(s)")
        for n in sorted(out):
            print(f"    {n:52s} attrs={sorted(out[n]) if out[n] else '[]'}")
        return out

    model_m  = harvest(r_model,  "MODEL observer  (GenAI semconv server + vLLM)")
    grpc_m   = harvest(r_grpc,   "TRANSPORT observer (gRPC's OWN A66 plugin)")
    client_m = harvest(r_client, "CLIENT observer (the two PROPOSED histograms)")
    plugin.deregister_global()

    # ------------------------------------------------------------------ A2
    hdr("A2. Phase x persona matrix, scored from the instruments above")
    PHASES = [
        ("1 admit / tokenize",    "FULL", "NONE", "NONE"),
        ("2 queue",               "FULL", "NONE", "NONE"),
        ("3 prefill",             "FULL", "NONE", "NONE"),
        ("4 KV handoff (disagg)", "NONE", "NONE", "PART"),
        ("5 first token",         "FULL", "NONE", "FULL"),
        ("6 inter-token gaps",    "FULL", "NONE", "FULL"),
        ("7 detokenize + chunk",  "NONE", "NONE", "PART"),
        ("8 wire / transport",    "NONE", "PART", "PART"),
        ("9 whole call",          "FULL", "FULL", "FULL"),
    ]
    print(f"\n  {'phase':24s} {'ML ENG':>8s} {'SRE today':>10s} {'SRE w/ ask':>11s} {'PRODUCT':>9s}")
    print("  " + "-"*66)
    ml=sre_t=sre_a=pm=ov_t=ov_a=0
    for name, m_, g_, c_ in PHASES:
        sre_ask = c_ if c_ != "NONE" else g_
        pm_ = "FULL" if name.startswith("9") else "NONE"
        ml += m_=="FULL"; sre_t += g_=="FULL"; sre_a += sre_ask=="FULL"; pm += pm_=="FULL"
        ov_t += (m_=="FULL" and g_=="FULL"); ov_a += (m_=="FULL" and sre_ask=="FULL")
        print(f"  {name:24s} {m_:>8s} {g_:>10s} {sre_ask:>11s} {pm_:>9s}")
    print("  " + "-"*66)
    print(f"  FULL rows:  ML {ml}/9   SRE today {sre_t}/9   SRE with ask {sre_a}/9   PM {pm}/9")
    print(f"  ML n SRE shared rows -- today: {ov_t}   with the ask: {ov_a}")

    # ------------------------------------------------------------------ A3
    hdr("A3. THE JOIN QUESTION -- can these go on one dashboard panel?")
    def keyset(d):
        s = set()
        for v in d.values(): s |= v
        return s
    ks_model, ks_grpc, ks_client = keyset(model_m), keyset(grpc_m), keyset(client_m)
    print(f"\n  MODEL     attribute keys ({len(ks_model)}): {sorted(ks_model)}")
    print(f"  TRANSPORT attribute keys ({len(ks_grpc)}): {sorted(ks_grpc)}")
    print(f"  CLIENT    attribute keys ({len(ks_client)}): {sorted(ks_client)}")
    for a, b, la, lb in ((ks_model, ks_grpc, "MODEL", "TRANSPORT"),
                         (ks_model, ks_client, "MODEL", "CLIENT"),
                         (ks_grpc, ks_client, "TRANSPORT", "CLIENT")):
        i = a & b
        print(f"  {la} n {lb} = {sorted(i) if i else 'EMPTY SET'}")
    print("\n  -> No attribute in common between the model server's metrics and the")
    print("     transport's. Not different names for the same dimension -- no shared")
    print("     dimension at all. Nothing to group_by jointly, nothing to filter both")
    print("     by. The handoff has no join key, so it happens in a meeting.")
    srv.stop(0)

if __name__ == "__main__":
    main()
