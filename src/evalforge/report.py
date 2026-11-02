"""Report writers: console, JSON, JUnit XML, Markdown and a self-contained HTML page."""

from __future__ import annotations

import html
import json
import os
import sys
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .core import CaseResult, SuiteResult

_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def print_case(result: CaseResult) -> None:
    mark = _c("32", "PASS") if result.passed else _c("31", "FAIL")
    flaky = f" (flaky: {result.attempt_pass_rate:.0%} of attempts passed)" if 0 < result.attempt_pass_rate < 1 else ""
    print(f"  {mark}  {result.id}  {_c('2', f'{result.latency_ms:.0f}ms')}{flaky}")
    if result.error:
        print(f"        {_c('31', 'error:')} {result.error}")
    for a in result.assertions:
        if not a.passed and not a.skipped:
            print(f"        {_c('31', '✗')} {a.type}: {a.reason}")


def print_summary(result: SuiteResult) -> None:
    s = result.to_dict()["summary"]
    skipped = sum(1 for c in result.cases for a in c.assertions if a.skipped)
    status = _c("32", "OK") if result.ok else _c("31", "FAILED")
    print()
    print(
        f"{result.name}: {s['passed']}/{s['total']} passed ({s['pass_rate']:.1%}, "
        f"threshold {result.threshold:.0%})  p50 {s['latency_p50_ms']:.0f}ms  p95 {s['latency_p95_ms']:.0f}ms  "
        f"in {result.duration_s:.1f}s  [{status}]"
    )
    if skipped:
        print(_c("33", f"  {skipped} assertion(s) skipped (e.g. llm_rubric without an API key)"))


def write_json(result: SuiteResult, path: str | Path) -> None:
    Path(path).write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")


def write_junit(result: SuiteResult, path: str | Path) -> None:
    suite = ET.Element(
        "testsuite",
        name=result.name,
        tests=str(result.total),
        failures=str(sum(1 for c in result.cases if not c.passed and not c.error)),
        errors=str(sum(1 for c in result.cases if c.error)),
        time=f"{result.duration_s:.3f}",
    )
    for c in result.cases:
        tc = ET.SubElement(suite, "testcase", classname=result.name, name=c.id, time=f"{c.latency_ms / 1000:.3f}")
        if c.error:
            ET.SubElement(tc, "error", message=c.error)
        elif not c.passed:
            failed = [a for a in c.assertions if not a.passed and not a.skipped]
            msg = "; ".join(f"{a.type}: {a.reason}" for a in failed)
            el = ET.SubElement(tc, "failure", message=msg[:500])
            el.text = f"input: {c.input}\n\noutput: {c.output}"
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


def to_markdown(result: SuiteResult) -> str:
    s = result.to_dict()["summary"]
    icon = "✅" if result.ok else "❌"
    lines = [
        f"## {icon} evalforge: {result.name}",
        "",
        f"**{s['passed']}/{s['total']} passed** ({s['pass_rate']:.1%}, threshold {result.threshold:.0%}) · "
        f"p50 {s['latency_p50_ms']:.0f} ms · p95 {s['latency_p95_ms']:.0f} ms · target `{result.target}`",
        "",
    ]
    failed = [c for c in result.cases if not c.passed]
    if failed:
        lines += ["| test | failed check | reason |", "|---|---|---|"]
        for c in failed:
            if c.error:
                lines.append(f"| `{c.id}` | error | {_md(c.error)} |")
            for a in c.assertions:
                if not a.passed and not a.skipped:
                    lines.append(f"| `{c.id}` | {a.type} | {_md(a.reason)} |")
    return "\n".join(lines) + "\n"


def _md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")[:200]


def write_html(result: SuiteResult, path: str | Path) -> None:
    data = result.to_dict()
    Path(path).write_text(_HTML.replace("__DATA__", _safe_json(data)).replace("__TITLE__", html.escape(result.name)), encoding="utf-8")


def _safe_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ · evalforge report</title>
<style>
:root{--bg:#f7f7f8;--card:#fff;--fg:#1d1d22;--muted:#6b6b76;--line:#e4e4e8;--pass:#13854e;--fail:#c4302b;--skip:#a26b00;--accent:#4f46e5}
@media (prefers-color-scheme:dark){:root{--bg:#111114;--card:#1b1b20;--fg:#ececf1;--muted:#9a9aa6;--line:#2c2c33;--pass:#3ecf8e;--fail:#ff6b63;--skip:#f5b83d;--accent:#8b85ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px}h1{font-size:22px;margin:0 0 4px}.sub{color:var(--muted);margin-bottom:20px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:20px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}.kpi b{display:block;font-size:24px}.kpi span{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.bar{height:8px;border-radius:4px;background:var(--line);overflow:hidden;margin-top:8px}.bar i{display:block;height:100%;background:var(--pass)}
.controls{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}input,select{background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font:inherit}input{flex:1;min-width:180px}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;margin-bottom:8px}summary{cursor:pointer;padding:10px 14px;display:flex;gap:10px;align-items:center;list-style:none}summary::-webkit-details-marker{display:none}
.badge{font-size:11px;font-weight:700;padding:2px 8px;border-radius:999px;color:#fff}.p{background:var(--pass)}.f{background:var(--fail)}
.id{font-family:ui-monospace,Consolas,monospace;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.ms{color:var(--muted);font-size:12px}
.tag{font-size:11px;color:var(--accent);border:1px solid var(--line);border-radius:999px;padding:0 6px}
.body{padding:0 14px 14px;border-top:1px solid var(--line)}.body h4{margin:12px 0 4px;font-size:12px;color:var(--muted);text-transform:uppercase}
pre{white-space:pre-wrap;word-break:break-word;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px;margin:0;font:12px/1.5 ui-monospace,Consolas,monospace}
table{width:100%;border-collapse:collapse}td{padding:6px 4px;border-bottom:1px solid var(--line);vertical-align:top}td:first-child{width:24px}
.ok{color:var(--pass)}.no{color:var(--fail)}.sk{color:var(--skip)}footer{color:var(--muted);font-size:12px;margin-top:24px;text-align:center}
</style></head><body><main>
<h1 id="t"></h1><div class="sub" id="s"></div><div class="kpis" id="k"></div>
<div class="controls"><input id="q" placeholder="Filter by id, input or output…"><select id="st"><option value="">All results</option><option value="fail">Failed only</option><option value="pass">Passed only</option></select><select id="tg"><option value="">All tags</option></select></div>
<div id="list"></div><footer>Generated by evalforge</footer></main>
<script>
const D=__DATA__,S=D.summary,$=id=>document.getElementById(id),esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
$("t").textContent=D.name;$("s").textContent=`target ${D.target} · ${D.started_at} · ${D.duration_s}s · ${S.ok?"PASSED":"FAILED"} (threshold ${Math.round(D.threshold*100)}%)`;
const kp=[["Pass rate",(S.pass_rate*100).toFixed(1)+"%",S.pass_rate],["Passed",S.passed+" / "+S.total],["Failed",S.failed],["p50 latency",Math.round(S.latency_p50_ms)+" ms"],["p95 latency",Math.round(S.latency_p95_ms)+" ms"]];
$("k").innerHTML=kp.map(([l,v,r])=>`<div class="kpi"><span>${l}</span><b>${v}</b>${r!==undefined?`<div class="bar"><i style="width:${r*100}%"></i></div>`:""}</div>`).join("");
const tags=[...new Set(D.cases.flatMap(c=>c.tags))].sort();$("tg").innerHTML+=tags.map(t=>`<option>${esc(t)}</option>`).join("");
function row(a){const cls=a.skipped?"sk":a.passed?"ok":"no",sym=a.skipped?"–":a.passed?"✓":"✗";return `<tr><td class="${cls}">${sym}</td><td><b>${esc(a.type)}</b> <span class="ms">${a.skipped?"skipped":a.score.toFixed(2)}</span><br>${esc(a.reason)}</td></tr>`}
function draw(){const q=$("q").value.toLowerCase(),st=$("st").value,tg=$("tg").value;
const cs=D.cases.filter(c=>(!st||(st==="pass")===c.passed)&&(!tg||c.tags.includes(tg))&&(!q||(c.id+c.input+c.output).toLowerCase().includes(q))).sort((a,b)=>a.passed-b.passed);
$("list").innerHTML=cs.map(c=>`<details${c.passed?"":" open"}><summary><span class="badge ${c.passed?"p":"f"}">${c.passed?"PASS":"FAIL"}</span><span class="id">${esc(c.id)}</span>${c.tags.map(t=>`<span class="tag">${esc(t)}</span>`).join("")}<span class="ms">${Math.round(c.latency_ms)} ms</span></summary>
<div class="body"><h4>Input</h4><pre>${esc(c.input)}</pre><h4>Output</h4><pre>${esc(c.error?"ERROR: "+c.error:c.output)}</pre>${c.attempt_pass_rate<1?`<h4>Flaky</h4><div>${Math.round(c.attempt_pass_rate*100)}% of attempts passed</div>`:""}
<h4>Checks</h4><table>${c.assertions.map(row).join("")||"<tr><td></td><td>none</td></tr>"}</table></div></details>`).join("")||"<p>No matching tests.</p>"}
["q","st","tg"].forEach(id=>$(id).addEventListener("input",draw));draw();
</script></body></html>
"""
