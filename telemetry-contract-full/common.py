"""Shared rig: locked cost model + server phase ledger + client observers."""
import time, math, statistics, threading, uuid, collections
import grpc
import inference_pb2 as pb
import inference_pb2_grpc as pbg

# ---- LOCKED cost model (reproduces the headline figures) ----
PREFILL_MS_PER_TOKEN = 0.20
DECODE_MS_PER_TOKEN  = 1.68
TOKENS_PER_CHUNK     = 8
ADMIT_MS             = 0.8
DETOK_MS_PER_CHUNK   = 0.05

LOCKED = {
    "prefill_heavy": dict(prompt_tokens=4000, output_tokens=128),
    "decode_heavy":  dict(prompt_tokens=64,   output_tokens=624),
}

def busy(ms):
    if ms <= 0: return
    t_end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < t_end:
        pass

def pct(xs, p):
    if not xs: return float("nan")
    s = sorted(xs)
    k = (len(s) - 1) * (p / 100.0)
    f = math.floor(k); c = math.ceil(k)
    if f == c: return s[int(k)]
    return s[f] * (c - k) + s[c] * (k - f)

# --------------------------------------------------------------------------
class InferenceServicer(pbg.InferenceServicer):
    """Emits a token stream and records a TRUE phase ledger (server ground truth)."""
    def __init__(self):
        self.ledgers = {}
        self.lock = threading.Lock()
        self.admission = threading.Semaphore(999)

    def Generate(self, req, context):
        t0 = time.perf_counter()
        rid = None
        for k, v in context.invocation_metadata():
            if k == "x-request-id": rid = v
        rid = rid or str(uuid.uuid4())

        busy(ADMIT_MS); t_admitted = time.perf_counter()
        busy(req.queue_ms); t_scheduled = time.perf_counter()

        prefill_ms = req.prompt_tokens * PREFILL_MS_PER_TOKEN * (req.prefill_scale or 1.0)
        busy(prefill_ms); t_prefilled = time.perf_counter()

        disagg = req.kv_transfer_ms > 0
        t_kv = t_prefilled

        dec_ms = DECODE_MS_PER_TOKEN * (req.decode_scale or 1.0)
        n = req.output_tokens
        tok_times = []
        buf = 0; seq = 0; detok = 0.0
        first_token_t = None
        for i in range(n):
            busy(dec_ms)
            if req.stall_after and i == req.stall_after:
                busy(req.stall_ms)
            tok_times.append(time.perf_counter())
            if first_token_t is None: first_token_t = tok_times[-1]
            buf += 1
            if disagg and i == 0:
                # DISAGGREGATED: token 0 is produced on the PREFILL worker and
                # ships alone; the KV cache is then transferred to the decode
                # worker, which produces token 1 onward.
                d0 = time.perf_counter(); busy(DETOK_MS_PER_CHUNK)
                detok += (time.perf_counter()-d0)*1000
                yield pb.Chunk(seq=seq, tokens=buf, text="x"*(buf*4))
                seq += 1; buf = 0
                busy(req.kv_transfer_ms); t_kv = time.perf_counter()
                continue
            if buf == TOKENS_PER_CHUNK or i == n - 1:
                d0 = time.perf_counter(); busy(DETOK_MS_PER_CHUNK)
                detok += (time.perf_counter() - d0) * 1000
                yield pb.Chunk(seq=seq, tokens=buf, text="x" * (buf * 4))
                seq += 1; buf = 0
        t_end = time.perf_counter()

        # TPOT as the DECODE WORKER measures it: gaps between ITS tokens only.
        # Token 0 belongs to the prefill worker, so the token0->token1 interval
        # (which contains the KV transfer) is in neither server's definition.
        first_decode_idx = 2 if disagg else 1
        gaps = [(tok_times[i] - tok_times[i-1]) * 1000
                for i in range(first_decode_idx, len(tok_times))]
        led = pb.Ledger(
            admit_ms=(t_admitted - t0) * 1000,
            queue_ms=(t_scheduled - t_admitted) * 1000,
            prefill_ms=(t_prefilled - t_scheduled) * 1000,
            kv_ms=(t_kv - t_prefilled) * 1000,
            first_token_ms=(first_token_t - t_prefilled) * 1000,
            decode_ms=(tok_times[-1] - first_token_t) * 1000,
            detok_ms=detok,
            out_tokens=n,
            ttft_ms=(first_token_t - t0) * 1000,
            tpot_ms=(statistics.fmean(gaps) if gaps else 0.0),
            tpot_median_ms=(statistics.median(gaps) if gaps else 0.0),
        )
        with self.lock:
            self.ledgers[rid] = led

    def GetLedger(self, req, context):
        with self.lock:
            return self.ledgers.get(req.request_id, pb.Ledger())

def start_server(port=0, servicer=None, plugins=None):
    from concurrent import futures
    s = grpc.server(futures.ThreadPoolExecutor(max_workers=16))
    servicer = servicer or InferenceServicer()
    pbg.add_InferenceServicer_to_server(servicer, s)
    p = s.add_insecure_port(f"127.0.0.1:{port}")
    s.start()
    return s, servicer, p

# --------------------------------------------------------------------------
class ClientObserver:
    """The transport-boundary view: the four numbers, stamped on message receipt."""
    def __init__(self):
        self.ttfm = []; self.gaps = []; self.dur = []; self.msgs = []

    def run(self, stub, req, rid):
        md = (("x-request-id", rid),)
        t0 = time.perf_counter()
        first = None; last = None; n = 0
        for _ in stub.Generate(req, metadata=md):
            t = time.perf_counter()
            if first is None:
                first = t; self.ttfm.append((t - t0) * 1000)
            else:
                self.gaps.append((t - last) * 1000)
            last = t; n += 1
        self.dur.append((time.perf_counter() - t0) * 1000)
        self.msgs.append(n)
        return n

def mk_req(workload, **over):
    cfg = dict(LOCKED[workload]); cfg.update(over)
    return pb.GenerateRequest(workload=workload, model=over.get("model", "llama-3.1-8b"), **cfg)
