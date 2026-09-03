# Tracing AI Calls End to End

Companion repository for the gRPConf North America 2026 talk
**"Tracing AI Calls End to End: What AI inference teaches us about gRPC observability"**
by Jigar Savla · Sep 3, 2026 · 15:30 · Level 2 | Lovelace · gRPC + AI track

The talk's claim in one paragraph: AI inference did not invent new observability
problems. It made an old one impossible to ignore. `call.duration` was never enough,
and streaming is where that finally hurts. Time to first token, inter-token latency and
token counts are ordinary streaming-RPC ideas wearing AI clothes: time to first message,
inter-message gap, message counts. The transport can emit all of them without knowing
anything about models.

## The demo

Two requests. Same duration. Nothing alike.

![Two requests with the same call.duration and time to first message 18x apart](demo/demo.gif)

[Play the full recording (demo/demo.mp4, 1:50)](demo/demo.mp4) · [How it was made](demo/)

## What's here

| folder | what it is |
|---|---|
| [`telemetry-contract-test/`](telemetry-contract-test/) | **The QR target from the handout.** A CI test for your dashboard's assumptions. It checks whether each instrument you depend on is emitted, carries its required attributes, sits on a stable convention, ships with documented bucket advice, and whether the model side and the transport side can be joined at all. Compact version: passes 4 of 11 on my stack. |
| [`telemetry-contract-full/`](telemetry-contract-full/) | The 17-check original (passes 8 of 17), plus the fault matrix, the three-answers-to-one-question experiment, and the stability census behind the "one trace, two audiences" slide. |
| [`tail-sampling-repro/`](tail-sampling-repro/) | The code behind "detection inverts past `decision_wait`". A re-implementation of the Collector's tail-sampling processor, written from its README line by line. Run it against a real Collector and tell me where it diverges. |
| [`demo/`](demo/) | The recording, an animated preview, an offline player, the final-frame still, and the rig and build script that produce all of them. |
| [`handout/`](handout/) | The one-page handout given out at the talk. |

## How this relates to the OpenTelemetry GenAI semantic conventions

This work **complements** [`gen_ai.*`](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-metrics.md). It does not compete with it.

**Where they overlap.** Both sides time the same request, and both have a
"time to first" concept: `gen_ai.server.time_to_first_token` on the model's clock, and
the proposed `time_to_first_message` on the transport's clock. They are different
clocks and both are correct. Measured on one request they differed by 19.5 ms, which
was 127% of the model-side number. The only phase both cover today is the whole call.

**Where they don't.**

- **The attributes share nothing.** The Required attributes on `gen_ai.server.*`
  (`gen_ai.operation.name`, `gen_ai.provider.name`) and gRPC's A66 attributes
  (`grpc.method`, `grpc.target`, `grpc.status`) have an empty intersection. There is no
  key on which to join the model side and the transport side. The contract test's C5
  check proves this on a live export.
- **A transport cannot emit `gen_ai.*` correctly.** It can satisfy 0 of the 2 Required
  attributes, because only something that knows it is serving a model can. That is the
  measured reason the proposal is a gRFC and not a semconv PR.
- **Neither `gen_ai.server.*` metric covers the KV-cache handoff** in disaggregated
  serving. The prefill worker's TTFT ends before it. The decode worker's TPOT averages
  after it.
- **Status.** Every `gen_ai.*` metric is Development status. OTel's versioning spec says
  long-term dependencies SHOULD NOT be taken against Development signals. The docs also
  moved to the `semantic-conventions-genai` repo in 2026, and the links here point at
  the living copy.

One piece of this repo is a direct contribution *to* the GenAI conventions. The bucket
boundaries the convention documents in prose (12 metrics, 153 numbers) are absent from
the YAML its code generator reads, so the standard SDK path exports default buckets. A
true 14 ms p50 reports as roughly 2,500 ms. The contract test's C4 check shows it. That
is a small PR, and it belongs in their repo, not this one.

## The one action

Compare your tail sampler's `decision_wait` (default **30 s**) to your **p99 stream
duration**. If the second number is bigger, your latency policy is keeping your fast
requests and dropping your slow ones. Then set
`OTEL_METRICS_EXEMPLAR_FILTER=always_on`. The first fix is what makes the second one
worth having.

## Primary sources

[gRFC A66](https://github.com/grpc/proposal/blob/master/A66-otel-stats.md) ·
[A79](https://github.com/grpc/proposal/blob/master/A79-non-per-call-metrics-architecture.md) ·
[A96](https://github.com/grpc/proposal/blob/master/A96-retry-otel-stats.md) ·
[A108](https://github.com/grpc/proposal/blob/master/A108-otel-custom-per-call-label.md) ·
[A80](https://github.com/grpc/proposal/blob/master/A80-tcp-telemetry.md) ·
[OTel GenAI metrics (semantic-conventions-genai)](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-metrics.md) ·
[tail sampling processor](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/processor/tailsamplingprocessor/README.md) ·
[vLLM v1 metrics design](https://docs.vllm.ai/en/v0.8.5/design/v1/metrics.html)

*All measurements in this repo are loopback, single host, CPython, on a simulated cost
model. Python-build findings are labelled as such. GenAI semconv is Development status.*
