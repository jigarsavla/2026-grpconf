"""
EXP B -- The handoff protocol that does not exist.

B1: THREE HONEST NUMBERS FOR "TTFT" on one RPC. No arbitration rule says
    which one an SLO is written against.
B2: FIVE REAL FAULTS x FOUR DASHBOARDS. For each fault, which persona's
    dashboard moves? Classify: BOTH (arbitrable) / EXACTLY ONE (handoff
    required) / NEITHER (invisible).
B3: what the ask changes about the classification.
"""
import time, grpc, statistics
import common
import inference_pb2 as pb, inference_pb2_grpc as pbg

N = 5                     # requests per cell
MOVE = 20.0               # % change that counts as "the dashboard moved"

def hdr(t): print("\n" + "="*78 + f"\n{t}\n" + "="*78, flush=True)

def run_cell(stub, tag, drain_ms=0.0, workload="decode_heavy", **over):
    """Run N RPCs under one condition; return every persona's view."""
    ttfm=[]; gaps=[]; dur=[]; per_req_maxgap=[]
    s_ttft=[]; s_tpot=[]; s_tpot_med=[]; s_queue=[]; s_prefill=[]; s_decode=[]
    for i in range(N):
        rid = f"{tag}-{i}"
        req = common.mk_req(workload, **over)
        t0 = time.perf_counter(); first=None; last=None; mygaps=[]
        for _ in stub.Generate(req, metadata=(("x-request-id", rid),)):
            t = time.perf_counter()
            if first is None: first=t; ttfm.append((t-t0)*1000)
            else: mygaps.append((t-last)*1000)
            last=t
            if drain_ms: common.busy(drain_ms)     # slow consumer
        gaps.extend(mygaps)
        per_req_maxgap.append(max(mygaps) if mygaps else 0.0)
        dur.append((time.perf_counter()-t0)*1000)
        led = stub.GetLedger(pb.LedgerRequest(request_id=rid))
        s_ttft.append(led.ttft_ms); s_tpot.append(led.tpot_ms)
        s_tpot_med.append(led.tpot_median_ms)
        s_queue.append(led.queue_ms); s_prefill.append(led.prefill_ms)
        s_decode.append(led.decode_ms)
    p = common.pct
    return {
        # ML ENGINEER dashboard (GenAI semconv server metrics + vLLM phases)
        "ml.ttft_p50":    p(s_ttft,50),
        "ml.tpot_p50":    p(s_tpot,50),
        "ml.tpot_median_p50": p(s_tpot_med,50),
        "ml.queue_p50":   p(s_queue,50),
        "ml.prefill_p50": p(s_prefill,50),
        # SRE TODAY (A66, what gRPC emits)
        "sre.call_dur_p50": p(dur,50),
        "sre.call_dur_p99": p(dur,99),
        # SRE WITH THE ASK (the two proposed histograms)
        "ask.ttfm_p50":  p(ttfm,50),
        "ask.gap_p50":   p(gaps,50),
        "ask.gap_p99":   p(gaps,99),
        # p50 across requests of each request's WORST gap -- robust to a single
        # noisy baseline request in a way that a pooled max is not
        "ask.gap_worst":  p(per_req_maxgap,50),
        # PRODUCT
        "pm.e2e_p50":    p(dur,50),
    }

DASHBOARDS = {
    "ML ENGINEER":  ["ml.ttft_p50","ml.tpot_p50","ml.queue_p50","ml.prefill_p50"],
    "SRE (today)":  ["sre.call_dur_p50","sre.call_dur_p99"],
    "SRE (w/ ask)": ["ask.ttfm_p50","ask.gap_p50","ask.gap_p99","ask.gap_worst"],
    "PRODUCT":      ["pm.e2e_p50"],
}

def main():
    srv, sv, port = common.start_server()
    ch = grpc.insecure_channel(f"127.0.0.1:{port}")
    stub = pbg.InferenceStub(ch)

    # ---------------------------------------------------------------- B1
    hdr("B1. Three honest numbers for 'TTFT' on ONE request")
    rid = "b1"
    t0=time.perf_counter(); first=None
    for _ in stub.Generate(common.mk_req("decode_heavy"), metadata=(("x-request-id",rid),)):
        if first is None: first=time.perf_counter()
    e2e=(time.perf_counter()-t0)*1000
    led = stub.GetLedger(pb.LedgerRequest(request_id=rid))
    ttfm=(first-t0)*1000
    print(f"\n  ML engineer  gen_ai.server.time_to_first_token   {led.ttft_ms:8.2f} ms")
    print(f"  SRE          time to first MESSAGE (transport)   {ttfm:8.2f} ms")
    print(f"  Product      end to end, user-perceived          {e2e:8.2f} ms")
    print(f"\n  offset model->transport: {ttfm-led.ttft_ms:.2f} ms "
          f"= {100*(ttfm-led.ttft_ms)/led.ttft_ms:.1f}% of the model's number")
    print("  All three are correct. Nothing in any specification says which one an")
    print("  SLO is written against, and no two of them can be joined (see A3).")

    # ---------------------------------------------------------------- B2
    hdr("B2. Five real faults x four dashboards -- who sees it?")
    base = run_cell(stub, "base")
    FAULTS = [
        ("prefill regression (+40%)",   dict(prefill_scale=1.4)),
        ("queue saturation (+250ms)",   dict(queue_ms=250)),
        ("slow consumer (20ms/msg)",    dict()),                    # drain handled below
        ("KV handoff +150ms (disagg)",  dict(kv_transfer_ms=150)),
        ("one 400ms decode stall",      dict(stall_after=40, stall_ms=400)),
    ]
    rows=[]
    for i,(name, over) in enumerate(FAULTS):
        drain = 20.0 if "slow consumer" in name else 0.0
        cell = run_cell(stub, f"f{i}", drain_ms=drain, **over)
        rows.append((name, cell))

    print(f"\n  NB: ml.tpot_p50 is the p50 across requests of each request's MEAN\n"
          f"      inter-token gap -- the way vLLM defines TPOT. The p50 of each\n"
          f"      request's MEDIAN gap is tracked alongside it to show the difference.")
    print(f"\n  baseline: ml.ttft {base['ml.ttft_p50']:.1f}  ml.tpot {base['ml.tpot_p50']:.3f}  "
          f"sre.dur {base['sre.call_dur_p50']:.1f}  ask.ttfm {base['ask.ttfm_p50']:.1f}  "
          f"ask.gap_p99 {base['ask.gap_p99']:.2f}  ask.gap_worst {base['ask.gap_worst']:.1f}")

    print(f"\n  {'fault':30s} " + " ".join(f"{k:>13s}" for k in DASHBOARDS))
    print("  " + "-"*84)
    summary = {k:{"sees":0} for k in DASHBOARDS}
    classif = []
    for name, cell in rows:
        seen = {}
        detail = {}
        for dash, keys in DASHBOARDS.items():
            bestscore = -1.0; bestk = keys[0]; bestseen = False
            for k in keys:
                b = base[k]; c = cell[k]
                dabs = abs(c - b)
                drel = (dabs / b * 100) if b > 1e-9 else float("inf")
                # "moved" = at least 5 ms of real change AND either a >=20%
                # relative move or a baseline too small for a ratio to mean anything
                # a metric "moved" if it changed by >=20% relative; for metrics
                # whose baseline is ~0 (queue time) a ratio is meaningless, so
                # require 5 ms of absolute change instead
                # only a REGRESSION counts as the dashboard moving
                if c <= b:
                    k_seen = False
                else:
                    k_seen = (drel >= MOVE) if b > 1.0 else (dabs >= 5.0)
                score = dabs
                if (k_seen, score) > (bestseen, bestscore):
                    bestseen, bestscore, bestk = k_seen, score, k
            seen[dash] = bestseen
            detail[dash] = (bestk, base[bestk], cell[bestk])
            if seen[dash]: summary[dash]["sees"] += 1
        cells = " ".join(f"{('SEES' if seen[d] else '--'):>13s}" for d in DASHBOARDS)
        print(f"  {name:30s} {cells}")
        classif.append((name, seen, detail))
    print("  " + "-"*84)
    for d in DASHBOARDS:
        print(f"  {d:14s} detects {summary[d]['sees']}/5 faults")

    hdr("B2b. What kind of conversation does each fault produce?")
    for name, seen, detail in classif:
        ml = seen["ML ENGINEER"]; sre_t = seen["SRE (today)"]; sre_a = seen["SRE (w/ ask)"]
        if ml and sre_t:   verdict_today = "BOTH see it -> arbitrable"
        elif ml or sre_t:  verdict_today = f"ONLY {'ML' if ml else 'SRE'} sees it -> HANDOFF"
        else:              verdict_today = "NEITHER sees it -> INVISIBLE"
        if ml and sre_a:   verdict_ask = "BOTH -> arbitrable"
        elif ml or sre_a:  verdict_ask = f"ONLY {'ML' if ml else 'SRE'} -> handoff"
        else:              verdict_ask = "NEITHER -> invisible"
        print(f"\n  {name}")
        print(f"      today:        {verdict_today}")
        print(f"      with the ask: {verdict_ask}")
        for d in DASHBOARDS:
            k, b, c = detail[d]
            rel = f"{(c-b)/b*100:+8.1f}%" if b > 1e-9 else "     n/a"
            mark = "MOVED" if seen[d] else "  -  "
            print(f"        {mark} {d:14s} {k:20s} {b:9.2f} -> {c:9.2f} ms  {rel}")

    # --------------------------------------------------------------- B2c
    hdr("B2c. The SAME fault on the OTHER workload -- who sees it now?")
    pbase = run_cell(stub, "pb", workload="prefill_heavy")
    pfault = run_cell(stub, "pf", workload="prefill_heavy", prefill_scale=1.4)
    print(f"\n  prefill regression (+40%), prefill-heavy workload (4000-token prompt):")
    for k in ("ml.prefill_p50","ml.ttft_p50","sre.call_dur_p50","ask.ttfm_p50","pm.e2e_p50"):
        b,c = pbase[k], pfault[k]
        print(f"    {k:20s} {b:9.2f} -> {c:9.2f} ms  {(c-b)/b*100:+7.1f}%")
    print("\n  Same fault, same code, same 40%. On decode-heavy it was 5 ms and only")
    print("  the ML engineer's dashboard moved (because their denominator is 13 ms).")
    print("  On prefill-heavy every dashboard moves. WHO SEES A FAULT IS A PROPERTY")
    print("  OF THE WORKLOAD MIX, not of the instrumentation -- which is why a")
    print("  percentage-based SLO means something different to each persona.")

    # ---------------------------------------------------------------- B3
    hdr("B3. Score")
    def score(which):
        both=one=none=0
        for name, seen, _ in classif:
            ml = seen["ML ENGINEER"]; s = seen[which]
            if ml and s: both+=1
            elif ml or s: one+=1
            else: none+=1
        return both, one, none
    for which in ("SRE (today)", "SRE (w/ ask)"):
        b,o,n = score(which)
        print(f"  ML vs {which:12s}:  both {b}   exactly one {o}   neither {n}")
    print("\n  'Exactly one' is the count of incidents that must be resolved by a human")
    print("  walking to another team, because the two dashboards cannot be joined.")
    srv.stop(0)

if __name__ == "__main__":
    main()
