"""Rebuild the demo asset: real RPCs -> asciicast v2 -> player -> mp4 + PNG.
Frame-accurate render (never real-time capture: that drifted 13.7% in an earlier build)."""
import sys, os, json, statistics, subprocess, shutil
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import grpc, rig

OUT = HERE  # assets are written beside this script
FRAMES = os.path.join(HERE, "_frames")
COLS, ROWS, BAR_W = 100, 30, 46
TARGET_S, HARD_CAP_S, TYPE_CPS, DILATION = 110.0, 120.0, 42.0, 9.0
SLOW_WPM, BREATH_S, FPS = 110.0, 0.9, 8
DIM, RST, BOLD, CYAN, YEL, GRN = "\x1b[2m", "\x1b[0m", "\x1b[1m", "\x1b[36m", "\x1b[33m", "\x1b[32m"

NARRATION = [("setup-prefill", 6), ("setup-decode", 10), ("runs-done", 7), ("reveal-table", 18),
             ("reveal-duration", 28), ("reveal-ttfm", 33), ("punchline", 20), ("caveat", 16)]


def bar(v, vmax, w=BAR_W):
    return "█" * (0 if vmax <= 0 else max(1, round(w * v / vmax)))


def capture(n=9):
    takes = []
    with rig.serving() as target:
        ch = grpc.insecure_channel(target); rig.warm(ch)
        for _ in range(n):
            a = rig.measure(ch, "prefill-heavy"); b = rig.measure(ch, "decode-heavy")
            takes.append(dict(prefill=a, decode=b, ratio=a["ttfm_ms"] / b["ttfm_ms"],
                              dur_delta=abs(a["duration_ms"] - b["duration_ms"]),
                              sep_pct=100 * abs(a["duration_ms"] - b["duration_ms"]) /
                                      ((a["duration_ms"] + b["duration_ms"]) / 2)))
    s = sorted(takes, key=lambda t: t["ratio"])
    return takes, s[len(s) // 2]          # the MEDIAN take, never the best


def table_for(ch):
    ph, dh = ch["prefill"], ch["decode"]
    dmax = max(ph["duration_ms"], dh["duration_ms"]); tmax = max(ph["ttfm_ms"], dh["ttfm_ms"])
    t = ["  workload        prompt   out    msgs   call.duration      TTFM", "  " + "-" * 66]
    for r in (ph, dh):
        t.append("  {:<14s} {:>6d} {:>5d} {:>6d} {:>13.1f} ms {:>9.1f} ms".format(
            r["workload"], r["prompt_tokens"], r["output_tokens"], r["messages"],
            r["duration_ms"], r["ttfm_ms"]))
    t += ["", "  call.duration  -- the number you already have"]
    for r in (ph, dh):
        t.append("    {:<14s}|{:<{w}s}| {:8.1f} ms".format(r["workload"],
                 bar(r["duration_ms"], dmax), r["duration_ms"], w=BAR_W))
    t.append("    {:<14s} {:>{w}s}   {:.1f} ms apart".format("", "↓", ch["dur_delta"], w=BAR_W))
    t += ["", "  TTFM (time to first message)  -- the number you want"]
    for r in (ph, dh):
        t.append("    {:<14s}|{:<{w}s}| {:8.1f} ms".format(r["workload"],
                 bar(r["ttfm_ms"], tmax), r["ttfm_ms"], w=BAR_W))
    t.append("    {:<14s} {:>{w}s}   {:.1f}x apart".format("", "↑", ch["ratio"], w=BAR_W))
    return t


class Cast:
    def __init__(self): self.t, self.ev, self.pauses = 0.0, [], []
    def out(self, s): self.ev.append([round(self.t, 6), "o", s])
    def line(self, s=""): self.out(s + "\r\n"); self.t += 0.05
    def wait(self, s): self.t += s
    def typed(self, s):
        for i in range(0, len(s), 4):
            self.out(s[i:i + 4]); self.t += 4 / TYPE_CPS
        self.out("\r\n"); self.t += 0.04
    def pause(self, label, words):
        self.ev.append([round(self.t, 6), "m", label])
        self.pauses.append(dict(label=label, words=words, idx=len(self.ev), secs=None))
    def allocate(self):
        fixed = self.t
        need = [p["words"] / SLOW_WPM * 60.0 + BREATH_S for p in self.pauses]
        slack = TARGET_S - fixed - sum(need); tw = sum(p["words"] for p in self.pauses)
        alloc = [n + max(0.0, slack) * (p["words"] / tw) for n, p in zip(need, self.pauses)]
        off = [0.0] * (len(self.ev) + 1)
        for p, a in zip(self.pauses, alloc):
            p["secs"] = a
            for i in range(p["idx"], len(self.ev) + 1): off[i] += a
        for i, e in enumerate(self.ev): e[0] = round(e[0] + off[i], 6)
        self.t = fixed + sum(alloc)
        return fixed, sum(need), slack


def build_cast(chosen, table):
    c = Cast(); ph, dh = chosen["prefill"], chosen["decode"]
    c.line(f"{DIM}# gRPConf NA 2026 -- two server-streaming RPCs, same service, same method{RST}")
    c.line(f"{DIM}# playback of the streaming section is slowed {DILATION:.0f}x so you can see "
           f"the messages land{RST}")
    c.line()
    c.typed(f"{CYAN}$ ./infer --workload prefill-heavy  --prompt 2000 --max-output 136{RST}")
    c.pause(*NARRATION[0])
    c.typed(f"{CYAN}$ ./infer --workload decode-heavy   --prompt   50 --max-output 624{RST}")
    c.pause(*NARRATION[1]); c.line()
    for r, label in ((ph, "prefill-heavy"), (dh, "decode-heavy")):
        c.line(f"{BOLD}  {label}{RST}"); c.out("    messages: ")
        n = r["messages"]; ttfm_s = r["ttfm_ms"] / 1000.0 * DILATION
        dur_s = r["duration_ms"] / 1000.0 * DILATION
        c.wait(ttfm_s); step = (dur_s - ttfm_s) / max(1, n - 1)
        for i in range(n):
            c.out("▪")
            if i == 0:
                c.out(f" {YEL}<- first message at {r['ttfm_ms']:.0f} ms{RST}\r\n              ")
            if i < n - 1: c.wait(step)
        c.line(f"\r\n    {n} messages, {r['output_tokens']} tokens, "
               f"call.duration {r['duration_ms']:.1f} ms")
        c.line()
    c.pause(*NARRATION[2])
    c.out("\x1b[2J\x1b[3J\x1b[H")                      # clear: the reveal is a clean frame
    c.line(f"{DIM}{'=' * 74}{RST}")
    c.line("  prefill-heavy   prompt 2000 tok -> 136 tok out, 17 messages")
    c.line("  decode-heavy    prompt   50 tok -> 624 tok out, 78 messages")
    c.line()
    for ln in table[:4]: c.line(ln)
    c.pause(*NARRATION[3])
    for ln in table[4:9]: c.line(ln)
    c.pause(*NARRATION[4])
    for ln in table[9:]: c.line(ln)
    c.pause(*NARRATION[5]); c.line()
    c.line(f"  {BOLD}{GRN}call.duration: indistinguishable ({chosen['sep_pct']:.1f}% apart)."
           f"   TTFM: {chosen['ratio']:.0f}x apart.{RST}")
    c.pause(*NARRATION[6]); c.line()
    c.line(f"{DIM}  loopback / single host / CPython / simulated cost model."
           f"  the apparatus is real; the GPU is not.{RST}")
    c.pause(*NARRATION[7])
    c.out("")                                          # hold the final frame
    return c


PLAYER = """<!doctype html><html><head><meta charset="utf-8"><title>%(title)s</title><style>
 html,body{margin:0;background:#15171b;color:#e8e8e8;font:16px/1.35 "DejaVu Sans Mono",monospace}
 #wrap{padding:18px 24px}#scr{white-space:pre;min-height:%(minh)dpx}
 #bar{position:fixed;left:0;right:0;bottom:0;height:5px;background:#2a2d33}
 #fill{height:100%%;width:0;background:#6ee696}
 .c36{color:#67d3e0}.c33{color:#e8c86e}.c32{color:#6ee696}.c2{color:#767c86}.c1{font-weight:700}
</style></head><body><div id="wrap"><div id="scr"></div></div><div id="bar"><div id="fill"></div></div>
<script>
const CAST=%(cast)s, DUR=%(dur)f;
const scr=document.getElementById('scr'),fill=document.getElementById('fill');
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function render(u){let raw='';
 for(const e of CAST){if(e[0]>u)break;if(e[1]==='o')raw+=e[2];}
 const ci=raw.lastIndexOf('\\x1b[2J'); if(ci>=0) raw=raw.slice(ci);
 raw=raw.replace(/\\x1b\\[[0-9;]*[HJK]/g,'').replace(/\\r\\n/g,'\\n');
 let open=0; const out=esc(raw).replace(/\\x1b\\[(\\d+)m/g,(m,n)=>{
   if(n==='0'){if(open>0){open--;return '</span>';}return '';} open++; return '<span class="c'+n+'">';});
 scr.innerHTML=out+'</span>'.repeat(open); fill.style.width=(100*Math.min(1,u/DUR))+'%%';}
let t0=null,paused=false,off=0,cur=0;
function frame(ts){if(t0===null)t0=ts;
 if(!paused){cur=(ts-t0)/1000+off; if(cur>DUR)cur=DUR; render(cur);} else {t0=ts-(cur-off)*1000;}
 requestAnimationFrame(frame);}
requestAnimationFrame(frame);
addEventListener('keydown',e=>{if(e.code==='Space'){paused=!paused;e.preventDefault();}
 if(e.code==='ArrowRight')off+=5; if(e.code==='ArrowLeft'){off-=5; if(cur+off<0)off=-cur;}});
window.seek=function(s){paused=true;cur=s;render(s);};
window.plainText=function(){return scr.innerText;};
</script></body></html>"""


def png(text, path):
    from PIL import Image, ImageDraw, ImageFont
    lines = text.split("\n")
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 18)
    cw, chh = 11, 26
    W = max(64, max(len(l) for l in lines)) * cw + 64
    H = len(lines) * chh + 56
    img = Image.new("RGB", (W, H), (21, 23, 27)); d = ImageDraw.Draw(img)
    for i, l in enumerate(lines):
        col = (235, 235, 235)
        if "apart on the number" in l or "indistinguishable" in l: col = (110, 230, 150)
        elif "the number you want" in l or "the number you already have" in l: col = (232, 200, 110)
        elif "apparatus is real" in l: col = (140, 148, 158)
        d.text((32, 28 + i * chh), l, font=font, fill=col)
    img.save(path)


def main():
    print("REBUILDING THE DEMO ASSET (deadline-paced rig)\n")
    takes, chosen = capture(9)
    ratios = [t["ratio"] for t in takes]; seps = [t["sep_pct"] for t in takes]
    print(f"  {len(takes)} real pairs. ratio  min {min(ratios):.2f}x  p50 "
          f"{statistics.median(ratios):.2f}x  max {max(ratios):.2f}x")
    print(f"                  separation  min {min(seps):.2f}%  p50 "
          f"{statistics.median(seps):.2f}%  max {max(seps):.2f}%")
    print(f"  'more than an order of magnitude' holds in "
          f"{sum(r >= 10 for r in ratios)}/{len(takes)} takes")
    print(f"  median take chosen: ratio {chosen['ratio']:.2f}x, "
          f"separation {chosen['sep_pct']:.2f}%\n")

    table = table_for(chosen)
    c = build_cast(chosen, table)
    fixed, need, slack = c.allocate()
    dur = c.t
    hdr = {"version": 2, "width": COLS, "height": ROWS, "idle_time_limit": 3.0,
           "title": "TTFM vs call.duration", "env": {"TERM": "xterm-256color"}}
    cast_path = os.path.join(OUT, "demo.cast")
    with open(cast_path, "w") as f:
        f.write(json.dumps(hdr) + "\n")
        for e in c.ev: f.write(json.dumps(e) + "\n")
    print(f"  cast: {len(c.ev)} events, fixed {fixed:.2f}s + narration floor {need:.2f}s "
          f"+ slack {slack:+.2f}s = {dur:.2f}s")
    assert dur <= HARD_CAP_S, f"OVER HARD CAP: {dur}"
    assert abs(dur - TARGET_S) < 0.01, dur
    for p in c.pauses:
        assert p["secs"] >= p["words"] / SLOW_WPM * 60.0, p
    print(f"  [PASS] duration {dur:.2f}s == target, under the {HARD_CAP_S:.0f}s cap")
    print(f"  [PASS] all 8 narration pauses fit their words at {SLOW_WPM:.0f} wpm")

    frame = ["", "  prefill-heavy   prompt 2000 tok -> 136 tok out, 17 messages",
             "  decode-heavy    prompt   50 tok -> 624 tok out, 78 messages", ""] + table + \
            ["", f"  call.duration: indistinguishable ({chosen['sep_pct']:.1f}% apart)."
                 f"   TTFM: {chosen['ratio']:.0f}x apart.", "",
             "  loopback / single host / CPython / simulated cost model."
             "  the apparatus is real; the GPU is not."]
    png("\n".join(frame), os.path.join(OUT, "demo_final_frame.png"))
    open(os.path.join(OUT, "demo_final_frame.txt"), "w").write("\n".join(frame) + "\n")

    player_path = os.path.join(OUT, "demo_player.html")
    open(player_path, "w").write(PLAYER % dict(title=hdr["title"], cast=json.dumps(c.ev),
                                               dur=dur, minh=ROWS * 22))
    print(f"  [PASS] player written, {os.path.getsize(player_path):,} bytes")

    shutil.rmtree(FRAMES, ignore_errors=True); os.makedirs(FRAMES)
    from playwright.sync_api import sync_playwright
    n = int(round(dur * FPS)) + 1
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1120, "height": 620})
        reqs = []; pg.on("request", lambda r: reqs.append(r.url))
        pg.goto("file://" + player_path); pg.evaluate("window.seek(0)")
        for k in range(n):
            pg.evaluate(f"window.seek({k / FPS})")
            pg.screenshot(path=os.path.join(FRAMES, f"f{k:05d}.jpg"), type="jpeg", quality=88)
        final = pg.evaluate("window.plainText()"); b.close()
    ext = [u for u in reqs if not u.startswith("file://")]
    print(f"  [{'PASS' if not ext else 'FAIL'}] player made {len(ext)} external requests")
    assert "\x1b[" not in final and "TTFM (time to first message)" in final
    print("  [PASS] no raw ANSI leaks; TTFM section renders")

    mp4 = os.path.join(OUT, "demo.mp4")
    r = subprocess.run(["ffmpeg", "-y", "-framerate", str(FPS), "-i",
                        os.path.join(FRAMES, "f%05d.jpg"), "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-crf", "20", "-vf", "fps=25", mp4],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]
    meta = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                      "format=duration:stream=width,height,codec_name",
                                      "-of", "json", mp4], capture_output=True,
                                     text=True).stdout)
    vdur = float(meta["format"]["duration"]); st = meta["streams"][0]
    print(f"  mp4: {st['codec_name']} {st['width']}x{st['height']} "
          f"{os.path.getsize(mp4):,} B  {vdur:.2f}s (cast {dur:.2f}s, {vdur-dur:+.2f}s)")
    assert abs(vdur - dur) < 1.0 and vdur < HARD_CAP_S
    print(f"  [PASS] video matches the cast to <1 s and is under the 2:00 cap")
    shutil.rmtree(FRAMES, ignore_errors=True)
    json.dump(dict(chosen=chosen, takes=takes, duration_s=dur,
                   video_s=vdur, table=table, frame=frame),
              open(os.path.join(OUT, "demo_report.json"), "w"), indent=1)
    print("\n  assets in deck/assets/: demo.mp4, demo_player.html, demo_final_frame.png, demo.cast")


if __name__ == "__main__":
    main()
