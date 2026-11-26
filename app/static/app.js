"use strict";
const $ = (id) => document.getElementById(id);
let currentId = null;
let currentReport = null;
let requestGeneration = 0;
const labels = {high:"高风险", medium:"中风险", low:"低风险", info:"提示"};
const confidence = {high:"高", medium:"中", low:"低"};
const categories = {documentation:"文档", startup:"依赖与启动", portability:"可移植性", secrets:"敏感信息"};
function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function status(message, error = false) {
  $("status").className = "status" + (error ? " error" : "");
  $("status").textContent = message;
}
async function api(url, options) {
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "请求失败，请检查输入后重试。");
  return body;
}
function findings() {
  const container = $("findings");
  container.replaceChildren();
  const list = currentReport.findings.filter(f => $("filter").value === "all" || f.category === $("filter").value);
  $("risk-count").textContent = String(list.length);
  if (!list.length) container.append(element("p", "当前范围内未发现此类问题。请结合扫描覆盖范围理解结果。", "no-findings"));
  for (const f of list) {
    const card = element("details", undefined, "finding");
    card.id = "finding-" + f.id;
    const summary = element("summary");
    summary.append(element("span", labels[f.severity], "badge " + f.severity));
    const title = element("span", f.title, "finding-title");
    title.append(element("span", (f.path ? f.path + (f.line ? ":" + f.line : "") : "仓库级检查") + " · " + categories[f.category], "finding-path"));
    summary.append(title);
    const body = element("div", undefined, "finding-body");
    body.append(element("p", f.explanation), element("p", "建议：" + f.suggestion));
    if (f.evidence) body.append(element("code", f.evidence));
    body.append(element("p", "规则 " + f.rule_id + " · 置信度：" + confidence[f.confidence], "meta"));
    if (f.source_url) {
      const a = element("a", "查看此 commit 的源码位置");
      a.href = f.source_url; a.target = "_blank"; a.rel = "noopener noreferrer"; body.append(a);
    }
    card.append(summary, body); container.append(card);
  }
}
function render(report) {
  currentReport = report;
  $("empty").classList.add("hidden"); $("report").classList.remove("hidden");
  $("repo-name").textContent = report.repository;
  $("metadata").textContent = `${report.branch} · commit ${report.commit_sha} · ${new Date(report.scanned_at).toLocaleString("zh-CN")} · 规则 ${report.rule_version}`;
  $("score").textContent = String(report.score); $("assessment").textContent = report.assessment;
  $("urgent").classList.toggle("hidden", !report.urgent);
  const notices = [];
  if (!report.coverage.complete) {
    notices.push("文件扫描不完整：分数暂定。", ...report.coverage.warnings);
    if (Object.keys(report.coverage.skipped).length) notices.push("请查看下方跳过原因。");
  }
  if (report.assessment_complete === false) notices.push(...report.assessment_notes);
  $("incomplete").classList.toggle("hidden", notices.length === 0);
  $("incomplete").textContent = notices.join(" ");
  $("download").href = `/api/scans/${currentId}/report`;
  $("dimensions").replaceChildren();
  for (const d of report.dimensions) {
    const row = element("div", undefined, "dimension");
    const label = element("div", undefined, "bar-label"); label.append(element("span", d.label), element("span", d.applicable ? `${d.score} / ${d.maximum}` : "不适用"));
    const track = element("div", undefined, "bar-track"); const bar = element("div", undefined, "bar-fill");
    // CSSOM assignment avoids inline HTML and never interpolates repository content.
    bar.style.width = (d.applicable ? 100 * d.score / d.maximum : 0) + "%";
    track.setAttribute("role", "meter"); track.setAttribute("aria-label", d.label); track.setAttribute("aria-valuenow", d.score); track.setAttribute("aria-valuemin", "0"); track.setAttribute("aria-valuemax", d.maximum);
    track.append(bar);
    const details = element("details"); details.append(element("summary", "查看评分依据")); const ul = element("ul"); for (const text of d.details) ul.append(element("li", text)); details.append(ul);
    row.append(label, track, details); $("dimensions").append(row);
  }
  const c = report.coverage;
  $("coverage").replaceChildren(element("span", `已扫描 ${c.scanned_files} 个文本文件`), element("span", `文本 ${(c.scanned_bytes / 1024).toFixed(1)} KB`), element("span", `生态：${report.ecosystems.join("、")}`), element("span", c.complete ? "文件扫描范围已完成" : "文件扫描范围不完整"));
  const details = element("details"); details.append(element("summary", "查看扫描范围与跳过原因"));
  details.append(element("p", "仅检查当前 commit；不扫描 Git 历史、子模块、LFS 实体、依赖和构建目录。不执行代码。归档条目：" + c.entries));
  for (const [reason, count] of Object.entries(c.skipped)) details.append(element("p", reason + "：" + count));
  for (const warning of c.warnings) details.append(element("p", warning));
  $("coverage").append(details); $("filter").value = "all"; findings();
  $("advice-result").replaceChildren(); $("advice").disabled = false;
}
async function poll(id, generation) {
  try {
    const result = await api(`/api/scans/${id}`);
    if (generation !== requestGeneration) return;
    if (result.status === "failed") throw new Error(result.error);
    if (result.report) { render(result.report); status("体检已完成。每项结果都可展开核实，分数基于静态规则。"); $("submit").disabled = false; return; }
    status(result.stage + "…"); setTimeout(() => poll(id, generation), 2000);
  } catch (error) { if (generation === requestGeneration) { status(error.message, true); $("submit").disabled = false; } }
}
$("scan-form").addEventListener("submit", async event => {
  event.preventDefault(); $("submit").disabled = true;
  requestGeneration++; const generation = requestGeneration;
  $("report").classList.add("hidden"); status("正在提交扫描任务…");
  try {
    const result = await api("/api/scans", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({repository_url:$("repository").value.trim()})});
    currentId = result.id; history.replaceState(null, "", "?scan=" + currentId); poll(currentId, generation);
  } catch (error) { status(error.message, true); $("submit").disabled = false; }
});
$("example").addEventListener("click", () => { $("repository").value = "https://github.com/pallets/flask"; $("repository").focus(); });
$("filter").addEventListener("change", findings);
$("advice").addEventListener("click", async () => {
  const id = currentId; $("advice").disabled = true; $("advice-result").textContent = "正在生成建议…";
  try {
    const result = await api(`/api/scans/${id}/advice`, {method:"POST"});
    if (currentId !== id) return;
    $("advice-result").replaceChildren(element("p", result.message));
    const ol = element("ol");
    for (const item of result.items) {
      const finding = currentReport.findings.find(f => f.id === item.finding_id);
      const li = element("li"); const a = element("a", item.action + " · " + finding.title);
      a.href = "#finding-" + item.finding_id;
      a.addEventListener("click", () => { $("filter").value = "all"; findings(); $("finding-" + item.finding_id).open = true; });
      li.append(a); ol.append(li);
    }
    $("advice-result").append(ol);
  } catch (error) { if (currentId === id) $("advice-result").textContent = error.message; }
  finally { if (currentId === id) $("advice").disabled = false; }
});
const saved = new URLSearchParams(location.search).get("scan");
if (saved && /^[a-f0-9]{32}$/.test(saved)) { currentId = saved; $("submit").disabled = true; poll(saved, requestGeneration); }
