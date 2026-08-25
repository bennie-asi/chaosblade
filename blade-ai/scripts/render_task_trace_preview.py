#!/usr/bin/env python3
"""Extract real tasks.db data and render a standalone trace preview HTML.

Reads ~/.blade-ai/memory/tasks.db (read-only), joins tasks/task_details/
task_spans, and emits a self-contained HTML with the data embedded.
"""
import html
import json
import re
import sqlite3
from pathlib import Path

DB = Path.home() / ".blade-ai" / "memory" / "tasks.db"
OUT = Path(__file__).resolve().parents[1] / "outputs" / "task-trace-preview.html"

conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

tasks = [dict(r) for r in conn.execute(
    "SELECT * FROM tasks ORDER BY gmt_create DESC LIMIT 60")]
details = {r["task_id"]: dict(r) for r in conn.execute(
    "SELECT * FROM task_details")}
spans_rows = [dict(r) for r in conn.execute(
    "SELECT * FROM task_spans ORDER BY id")]
conn.close()

spans_by_task: dict[str, list] = {}
for s in spans_rows:
    spans_by_task.setdefault(s["task_id"], []).append(s)


def jload(v):
    if not v or not isinstance(v, str):
        return None
    try:
        return json.loads(v)
    except Exception:
        return None


TIMELINE_RE = re.compile(r"^- \*\*(\d{2}:\d{2}:\d{2})\*\*\s*(.+)$")

payload = []
for t in tasks:
    tid = t["task_id"]
    d = details.get(tid, {})
    spec = jload(d.get("fault_spec")) or {}
    fault_target = spec.get("fault_target", "")
    fault_action = spec.get("fault_action", "")
    pm = jload(d.get("postmortem")) or {}
    ver = jload(d.get("verification")) or {}
    feas = jload(d.get("feasibility_report")) or {}
    tgt = jload(d.get("target")) or {}

    timeline = []
    md = pm.get("markdown", "") or ""
    for line in md.splitlines():
        m = TIMELINE_RE.match(line.strip())
        if m:
            timeline.append({"ts": m.group(1), "desc": m.group(2).strip()})

    payload.append({
        "task_id": tid,
        "task_state": t.get("task_state") or "",
        "operation": t.get("operation") or "",
        "stage": t.get("stage") or "",
        "gmt_create": (t.get("gmt_create") or "")[:19].replace("T", " "),
        "gmt_modified": (t.get("gmt_modified") or "")[:19].replace("T", " "),
        "use_case": spec.get("case_resource_path") or spec.get("use_case_name", ""),
        "user_desc": spec.get("user_description", ""),
        "scope": spec.get("scope", ""),
        "fault_target": fault_target,
        "fault_action": fault_action,
        "params": spec.get("params") or {},
        "duration_seconds": spec.get("duration_seconds"),
        "target_ns": tgt.get("namespace", ""),
        "target_names": tgt.get("names") or [],
        "safety_status": d.get("safety_status") or "",
        "overall": ver.get("overall") or "",
        "layer1": (ver.get("layer1") or {}).get("status", ""),
        "layer2": (ver.get("layer2") or {}).get("status", ""),
        "layer2_details": (ver.get("layer2") or {}).get("details", ""),
        "checklist": (ver.get("checklist") or {}).get("items") or [],
        "feasibility": feas.get("message", ""),
        "feasibility_severity": feas.get("severity", ""),
        "model_name": d.get("model_name") or "",
        "pm_summary": pm.get("summary", ""),
        "timeline": timeline,
        "spans": [{
            "node_name": s.get("node_name", ""),
            "start_time": s.get("start_time", 0.0),
            "duration_ms": s.get("duration_ms", 0),
            "token_input": s.get("token_input", 0),
            "token_output": s.get("token_output", 0),
            "tool_calls": s.get("tool_calls") or "[]",
            "error": s.get("error") or "",
        } for s in spans_by_task.get(tid, [])],
    })

DATA_JSON = json.dumps(payload, ensure_ascii=False)

HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>Blade-AI · 任务 Trace 数据预览（真实 SQLite 数据）</title>
<style>
  :root {
    --bg: #FAF8F5; --card: #FFFFFF; --ink: #292524; --sub: #78716C;
    --line: #E7E0D8; --accent: #EA580C; --accent-soft: #FFF1E8;
    --ok: #16A34A; --warn: #D97706; --err: #DC2626; --info: #2563EB;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--ink);
         font: 14px/1.6 -apple-system, "PingFang SC", "Helvetica Neue", sans-serif; }
  header { padding: 20px 28px 12px; border-bottom: 1px solid var(--line);
           background: var(--card); position: sticky; top: 0; z-index: 5; }
  header h1 { font-size: 18px; }
  header h1 span { color: var(--accent); }
  header p { color: var(--sub); font-size: 12px; margin-top: 2px; }
  .layout { display: grid; grid-template-columns: 380px 1fr; gap: 0;
            min-height: calc(100vh - 80px); }
  .list { border-right: 1px solid var(--line); background: var(--card);
          overflow-y: auto; max-height: calc(100vh - 80px); position: sticky; top: 80px; }
  .item { padding: 12px 16px; border-bottom: 1px solid var(--line);
          cursor: pointer; transition: background .12s; }
  .item:hover { background: var(--accent-soft); }
  .item.active { background: var(--accent-soft); box-shadow: inset 3px 0 0 var(--accent); }
  .item .row1 { display: flex; align-items: center; gap: 8px; }
  .item .tid { font-weight: 600; font-size: 13px; overflow: hidden;
               text-overflow: ellipsis; white-space: nowrap; flex: 1; }
  .item .meta { color: var(--sub); font-size: 12px; margin-top: 2px; }
  .badge { font-size: 11px; padding: 1px 8px; border-radius: 999px;
           font-weight: 600; white-space: nowrap; }
  .b-injected { background: #FEF3C7; color: #B45309; }
  .b-recovered, .b-success { background: #DCFCE7; color: #15803D; }
  .b-partial_recovered { background: #E0F2FE; color: #0369A1; }
  .b-failed { background: #FEE2E2; color: #B91C1C; }
  .b-waiting_input { background: #EDE9FE; color: #6D28D9; }
  .b-default { background: #F5F5F4; color: #57534E; }
  .detail { padding: 24px 28px; overflow-y: auto; max-height: calc(100vh - 80px); }
  .card { background: var(--card); border: 1px solid var(--line);
          border-radius: 10px; padding: 16px 20px; margin-bottom: 16px; }
  .card h2 { font-size: 13px; color: var(--sub); text-transform: uppercase;
             letter-spacing: .06em; margin-bottom: 10px; }
  .kv { display: grid; grid-template-columns: 130px 1fr; row-gap: 6px; font-size: 13px; }
  .kv dt { color: var(--sub); }
  .kv dd { word-break: break-all; }
  code { background: #F5F0EA; padding: 1px 6px; border-radius: 4px; font-size: 12px; }
  .timeline { position: relative; padding-left: 22px; }
  .timeline::before { content: ""; position: absolute; left: 6px; top: 4px;
                      bottom: 4px; width: 2px; background: var(--line); }
  .tl-item { position: relative; padding-bottom: 14px; }
  .tl-item::before { content: ""; position: absolute; left: -21px; top: 6px;
                     width: 10px; height: 10px; border-radius: 50%;
                     background: var(--accent); border: 2px solid var(--accent-soft); }
  .tl-ts { font-weight: 700; font-variant-numeric: tabular-nums; color: var(--accent); }
  .tl-desc { color: var(--ink); font-size: 13px; margin-top: 1px; }
  .step { display: flex; gap: 10px; padding: 8px 0; border-bottom: 1px dashed var(--line);
          font-size: 13px; }
  .step:last-child { border-bottom: none; }
  .glyph { width: 20px; text-align: center; font-weight: 700; }
  .g-passed { color: var(--ok); } .g-skipped { color: var(--sub); } .g-failed { color: var(--err); }
  .step .ev { color: var(--sub); font-size: 12px; margin-top: 2px; }
  .empty { color: var(--sub); font-size: 13px; font-style: italic; }
  .note { background: #FFFBEB; border: 1px solid #FDE68A; border-radius: 10px;
          padding: 12px 16px; font-size: 13px; color: #92400E; margin-bottom: 16px; }
  .waterfall { display: flex; flex-direction: column; gap: 6px; }
  .wf-row { display: grid; grid-template-columns: 200px 1fr 200px; align-items: center; gap: 10px; }
  .wf-label { font-size: 12px; color: var(--sub); text-align: right;
              overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .wf-track { background: #F5F0EA; border-radius: 4px; height: 22px; position: relative; }
  .wf-bar { position: absolute; top: 2px; bottom: 2px; border-radius: 3px;
            background: linear-gradient(90deg, var(--accent), #F97316);
            min-width: 4px; }
  .wf-meta { font-size: 11px; color: var(--sub); white-space: nowrap;
             overflow: hidden; text-overflow: ellipsis; }
  .summary-chips { display: flex; gap: 8px; flex-wrap: wrap; }
  .chip { background: var(--accent-soft); color: var(--accent); font-size: 12px;
          font-weight: 600; padding: 3px 10px; border-radius: 999px; }
</style>
</head>
<body>
<header>
  <h1><span>Blade-AI</span> · 任务 Trace 数据预览</h1>
  <p>数据源：~/.blade-ai/memory/tasks.db（真实数据，只读提取）·
     展示内置 TaskTrace 体系当前能提供的信息形态</p>
</header>
<div class="layout">
  <div class="list" id="list"></div>
  <div class="detail" id="detail"></div>
</div>
<script>
const DATA = __DATA__;

const badge = (state) => {
  const cls = ["injected","recovered","partial_recovered","failed","waiting_input"]
    .includes(state) ? "b-" + state : "b-default";
  return `<span class="badge ${cls}">${state || "?"}</span>`;
};
const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

function renderList() {
  document.getElementById("list").innerHTML = DATA.map((t, i) => `
    <div class="item" data-i="${i}" onclick="select(${i})">
      <div class="row1">
        <span class="tid">${esc(t.task_id)}</span>
        ${badge(t.task_state)}
      </div>
      <div class="meta">${esc(t.use_case || t.operation)} · ${esc(t.gmt_create)}</div>
    </div>`).join("");
}

function select(i) {
  document.querySelectorAll(".item").forEach(el =>
    el.classList.toggle("active", Number(el.dataset.i) === i));
  renderDetail(DATA[i]);
}

function renderDetail(t) {
  const faultLabel = [t.scope, t.fault_target, t.fault_action].filter(Boolean).join("-");
  const paramStr = Object.entries(t.params || {}).map(([k,v]) => `${k}=${v}`).join(", ");

  // 时间线（来自 postmortem 的 Timeline 段）——这是当前数据里唯一的"阶段序列"
  const timeline = t.timeline.length ? `
    <div class="card"><h2>执行时间线（postmortem Timeline）</h2>
      <div class="timeline">${t.timeline.map(s => `
        <div class="tl-item">
          <div class="tl-ts">${esc(s.ts)}</div>
          <div class="tl-desc">${esc(s.desc)}</div>
        </div>`).join("")}
      </div>
    </div>` : `
    <div class="card"><h2>执行时间线</h2>
      <div class="empty">该任务无时间线数据（postmortem 未生成或无 Timeline 段）</div>
    </div>`;

  // NodeSpan 瀑布——task_spans 表的真实节点级 span，按单调时钟偏移排布
  const fmtDur = (ms) => ms >= 1000 ? (ms/1000).toFixed(1) + "s" : Math.round(ms) + "ms";
  const parseTools = (raw) => { try { return JSON.parse(raw || "[]"); } catch (e) { return []; } };
  let spansHtml;
  if (t.spans.length) {
    const sorted = [...t.spans].sort((a, b) => a.start_time - b.start_time);
    const t0 = sorted[0].start_time;
    const total = Math.max(
      ...sorted.map(s => (s.start_time - t0) * 1000 + (s.duration_ms || 0)), 1);
    const totIn = sorted.reduce((a, s) => a + (s.token_input || 0), 0);
    const totOut = sorted.reduce((a, s) => a + (s.token_output || 0), 0);
    const totTools = sorted.reduce((a, s) => a + parseTools(s.tool_calls).length, 0);
    spansHtml = `
      <div class="summary-chips" style="margin-bottom:12px">
        <span class="chip">${sorted.length} spans</span>
        <span class="chip">Σ跨度 ${fmtDur(total)}</span>
        <span class="chip">tokens ${totIn}↓ ${totOut}↑</span>
        <span class="chip">工具调用 ×${totTools}</span>
      </div>
      <div class="waterfall">${sorted.map(s => {
        const off = (s.start_time - t0) * 1000 / total * 100;
        const w = Math.max((s.duration_ms || 0) / total * 100, 0.4);
        const tools = parseTools(s.tool_calls);
        const paused = (s.error || "").includes("interrupted");
        const barColor = s.error ? (paused ? "background:#A8A29E" : "background:var(--err)") : "";
        const mark = s.error ? (paused ? " · ⏸" : " · ✗") : "";
        return `
        <div class="wf-row" title="${esc(s.node_name)} · ${fmtDur(s.duration_ms || 0)}${tools.length ? " · " + tools.join(", ") : ""}${s.error ? " · " + esc(s.error) : ""}">
          <div class="wf-label">${esc(s.node_name)}</div>
          <div class="wf-track"><div class="wf-bar" style="left:${off}%;width:${w}%;${barColor}"></div></div>
          <div class="wf-meta">${fmtDur(s.duration_ms || 0)} · ${s.token_input || 0}↓${s.token_output || 0}↑${tools.length ? ` · ${tools.length}🔧` : ""}${mark}</div>
        </div>`;
      }).join("")}
      </div>
      <div class="ev" style="margin-top:8px">横轴为任务内相对时间（单调时钟偏移）·
        ⏸ = 等待用户确认的暂停段 · ✗ = 出错 · 悬停查看工具调用明细</div>`;
  } else {
    spansHtml = `<div class="empty">task_spans 表中该任务没有 span 记录</div>`;
  }

  // 验证清单
  const glyph = {passed: ["✓","g-passed"], skipped: ["—","g-skipped"], failed: ["✗","g-failed"]};
  const checklist = t.checklist.length ? t.checklist.map(c => {
    const [g, cls] = glyph[c.status] || ["?", "g-skipped"];
    return `<div class="step">
      <div class="glyph ${cls}">${g}</div>
      <div><div>检查项 ${c.step} · <b>${esc(c.status)}</b></div>
        <div class="ev">${esc(c.evidence)}</div></div>
    </div>`;
  }).join("") : `<div class="empty">无验证清单</div>`;

  document.getElementById("detail").innerHTML = `
    <div class="card">
      <h2>任务概览</h2>
      <div class="summary-chips" style="margin-bottom:10px">
        ${badge(t.task_state)}
        ${t.overall ? `<span class="chip">验证: ${esc(t.overall)}</span>` : ""}
        ${t.safety_status ? `<span class="chip">安全: ${esc(t.safety_status)}</span>` : ""}
      </div>
      <dl class="kv">
        <dt>任务 ID</dt><dd><code>${esc(t.task_id)}</code></dd>
        <dt>用例</dt><dd>${esc(t.use_case || "—")}</dd>
        <dt>用户意图</dt><dd>${esc(t.user_desc || "—")}</dd>
        <dt>故障类型</dt><dd>${esc(faultLabel || "—")}${paramStr ? ` · <code>${esc(paramStr)}</code>` : ""}</dd>
        <dt>目标</dt><dd>${esc(t.target_ns)}/${esc(t.target_names.join(", "))}</dd>
        <dt>持续时间</dt><dd>${t.duration_seconds ? t.duration_seconds + "s" : "—"}</dd>
        <dt>创建 / 结束</dt><dd>${esc(t.gmt_create)} → ${esc(t.gmt_modified)}</dd>
        <dt>模型</dt><dd>${t.model_name ? `<code>${esc(t.model_name)}</code>` : "—"}</dd>
        <dt>可行性</dt><dd>${esc(t.feasibility || "—")}</dd>
      </dl>
    </div>
    ${timeline}
    <div class="card"><h2>Node Span 瀑布（task_spans 表）</h2>${spansHtml}</div>
    <div class="card"><h2>验证清单（Layer1: ${esc(t.layer1 || "—")} · Layer2: ${esc(t.layer2 || "—")}）</h2>
      ${checklist}
      ${t.layer2_details ? `<div class="ev" style="margin-top:8px">${esc(t.layer2_details)}</div>` : ""}
    </div>
    ${t.pm_summary ? `<div class="card"><h2>复盘摘要</h2>
      <div style="font-size:13px">${esc(t.pm_summary)}</div></div>` : ""}
  `;
}

renderList();
select(0);
</script>
<div style="padding:16px 28px;color:#78716C;font-size:12px">
  <div class="note" style="max-width:900px">
    📌 数据实况说明：本页所有内容均来自真实 SQLite 数据（~/.blade-ai/memory/tasks.db）。
    自 span 落盘修复后，每个真实任务的「Node Span 瀑布」会记录全部被包装节点的
    起止时间、耗时、token 消耗与工具调用，并随任务执行实时写库——即
    /api/v1/metric/&lt;task_id&gt; 的 spans 字段所消费的数据形态。
    历史任务（修复前运行的）无 span 记录，无法回填。
  </div>
</div>
</body>
</html>
"""

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(HTML.replace("__DATA__", DATA_JSON), encoding="utf-8")
print(f"OK -> {OUT}  ({len(payload)} tasks)")
