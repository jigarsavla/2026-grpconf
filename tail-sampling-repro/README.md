# Tail sampling inverts on long streams

The experiment behind the talk's claim that a tail sampler with a latency policy
keeps your fast streams and drops your slow ones once a stream outlives
`decision_wait`.

`tailsampler.py` is a re-implementation of the OpenTelemetry Collector's
`tailsamplingprocessor`, written from its README. Every behaviour it implements is
quoted from the README on the line above the code, so you can check it line by line.
It is not the Collector binary. If you run this against a real Collector and get a
different answer, I want to know.

```bash
pip install grpcio opentelemetry-sdk opentelemetry-semantic-conventions
./run.sh            # all four experiments, about 6 minutes
python3 exp_b_tailsampling.py   # just the inversion, about 3 minutes
```

## What it shows

Latency policy set to keep everything slower than 1 second. `decision_wait` scaled
to 2 seconds so the whole run fits in minutes (the Collector default is 30 s;
multiply every duration by 15 to read it as production).

```
 stream    true dur   dur/dw   spans held    dur AS SEEN     policy
 (tokens)   (p50 ms)           at decision   by sampler      kept
 ----------------------------------------------------------------------
     400      751.4    0.38       5 of 5        751.5 ms    0/6
     700     1272.4    0.64       5 of 5       1272.5 ms    6/6
    1100     1971.4    0.99       5 of 5       1971.5 ms    6/6
    1800     3215.3    1.61       2 of 5         51.1 ms    0/6
    3600     6346.4    3.17       2 of 5         51.1 ms    0/6
    7200    12561.7    6.28       2 of 5         51.1 ms    0/6
```

A span reaches the sampler only when it ends. Once a stream runs longer than
`decision_wait`, the sampler decides while holding only the spans that finished
early, so it sees a 51 ms trace and drops a 12 second one. Turning on the decision
caches makes it worse: the drop gets remembered.

The four experiments: `exp_a` shows when each span becomes visible downstream,
`exp_b` is the inversion above, `exp_c` shows what happens to the exemplar pointers
that reference dropped traces, and `exp_d` prices the histograms and the sampler's
memory. Real measured output from one run is in `out/`.
