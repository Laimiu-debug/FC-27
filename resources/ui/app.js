"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
const number = value => Number(value || 0).toLocaleString("zh-CN");
const pages = { library: "候选库", build: "构建与合并", copies: "副本与恢复", settings: "工作区设置" };
const states = { prepared: "准备完成", applying: "应用中断", applied: "已应用", restoring: "恢复中断", restored: "已还原" };
let data = null;
let selected = new Set();
let checks = new Map();
let copyChecks = new Map();
let preview = null;
let detailsId = "";
let sending = false;
let snapshotKey = "";
let jobsKey = "";
let toastTimer = null;
let finishedJobs = new Set();
let historyOpen = false;
let connected = false;
let loadedOnce = false;
let currentPage = "";

function element(tag, className = "", text = null) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== null) node.textContent = String(text);
  return node;
}

function button(text, className, action) {
  const node = element("button", "btn " + className, text);
  node.type = "button";
  node.addEventListener("click", action);
  return node;
}

function showToast(message, error = false) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.remove("hidden");
  node.classList.toggle("error", error);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.add("hidden"), error ? 9000 : 4500);
}

function go(page) {
  const name = pages[page] ? page : "library";
  const switched = currentPage !== name;
  currentPage = name;
  location.hash = name;
  $$(".page").forEach(node => node.classList.toggle("hidden", node.id !== "page-" + name));
  $$("[data-page]").forEach(node => node.classList.toggle("active", node.dataset.page === name));
  $("#page-name").textContent = pages[name];
  if (switched) window.scrollTo(0, 0);
}

function busy() { return sending || Boolean(data?.busy) || Boolean(data?.closing) || !connected; }
function selectionKey() { return Array.from(selected).sort().join("|"); }
function currentPreview() { return preview && preview.key === selectionKey() ? preview.result : null; }

function updateControls() {
  const ready = Boolean(data?.workspace.ready);
  $("#exit-button").classList.toggle("hidden", !data?.desktop);
  $("#exit-button").disabled = !connected || Boolean(data?.closing);
  $("#refresh-button").disabled = busy();
  $("#open-register").disabled = busy() || !ready;
  $$("form button[type=submit]").forEach(node => { node.disabled = busy() || !ready; });
  $("#init-form button[type=submit]").disabled = busy() || ready || Boolean(data?.workspace.error);
  $("#build-form button[type=submit]").disabled = busy() || !ready || Boolean(data?.workspace.build_error);
  $("#compose-submit").disabled = busy() || !ready || !currentPreview()?.plans_mergeable;
  $$(".check-button,.status-button").forEach(node => { node.disabled = busy() || !ready; });
  $$(".restore-button").forEach(node => { node.disabled = busy() || !ready || node.dataset.restored === "true"; });
  $("#detail-check").disabled = busy() || !ready;
  $("#detail-rehearse").disabled = busy() || !ready;
  $("#selection-preview").disabled = busy() || selected.size < 2;
  $("#selection-compose").disabled = busy() || selected.size < 2;
  $("#compose-preview").disabled = busy() || selected.size < 2;
}

function setSelected(id, checked) {
  if (checked) selected.add(id); else selected.delete(id);
  preview = null;
  syncSelection();
  renderPreview();
}

function syncSelection() {
  $$("input[data-select]").forEach(node => { node.checked = selected.has(node.dataset.select); });
  $$(".candidate-card").forEach(node => node.classList.toggle("selected", selected.has(node.dataset.id)));
  $("#metric-selected").textContent = number(selected.size);
  $("#selection-label").textContent = "已选择 " + selected.size + " 个候选";
  $("#selection-bar").classList.toggle("hidden", selected.size === 0);
  updateControls();
}

function selectionCheckbox(entry) {
  const input = element("input", "checkbox");
  input.type = "checkbox";
  input.dataset.select = entry.id;
  input.setAttribute("aria-label", "选择 " + entry.title);
  input.checked = selected.has(entry.id);
  input.addEventListener("change", () => setSelected(entry.id, input.checked));
  return input;
}

function originText(entry) {
  const origin = data.details[entry.id]?.origin;
  if (origin?.kind === "composed") return "合并候选 · " + origin.parents.join(" + ");
  if (origin?.kind === "pipeline") {
    const module = data.workspace.modules.find(item => item.id === origin.module);
    return "模块构建 · " + (module?.title || origin.module);
  }
  return entry.id === "probe" ? "格式诊断实验 · 单字节修改" : "已登记的研究候选";
}

function statBlock(value, label) {
  const block = element("div");
  block.append(element("strong", "", number(value)), element("span", "", label));
  return block;
}

function renderLibrary() {
  const query = $("#search").value.trim().toLowerCase();
  const entries = data.entries.filter(entry => (entry.title + " " + entry.id).toLowerCase().includes(query));
  const grid = $("#candidate-grid");
  grid.replaceChildren();
  entries.forEach(entry => {
    const card = element("article", "candidate-card");
    card.dataset.id = entry.id;
    const top = element("div", "candidate-card-top");
    const icon = element("span", "candidate-icon", data.details[entry.id]?.origin?.kind === "composed" ? "◈" : "◇");
    const name = element("div", "candidate-name");
    name.append(element("h3", "", entry.title), element("div", "candidate-id", entry.id));
    top.append(icon, name, selectionCheckbox(entry));
    const stats = element("div", "candidate-stats");
    stats.append(statBlock(entry.assets_built, "资产"), statBlock(entry.changed_values, "数值修改"), statBlock(entry.changed_bytes, "变更字节"));
    const bottom = element("div", "candidate-bottom");
    const passed = checks.has(entry.id);
    const badge = element("span", "badge" + (passed ? " ok" : ""), passed ? "✓ 本次离线预检通过" : "离线预检待运行");
    if (passed) badge.title = "预检完成于 " + new Date(checks.get(entry.id)).toLocaleString();
    const actions = element("div", "button-row");
    const check = button("预检", "outline check-button", () => runAction({ action: "check", id: entry.id }));
    actions.append(button("详情", "subtle", () => openDetail(entry.id)), check);
    bottom.append(badge, actions);
    card.append(top, element("div", "candidate-source", originText(entry)), stats, bottom);
    grid.append(card);
  });
  $("#library-empty").classList.toggle("hidden", entries.length > 0);
  if (data.entries.length && !entries.length) {
    $("#library-empty h3").textContent = "没有匹配的候选";
    $("#library-empty p").textContent = "换一个名称或标识搜索。";
    $("#library-empty button").classList.add("hidden");
  } else {
    $("#library-empty h3").textContent = "从第一份玩法候选开始";
    $("#library-empty p").textContent = "构建研究方案，或登记已经生成的编译与加载副本。";
    $("#library-empty button").classList.remove("hidden");
  }
  $("#library-count").textContent = number(data.entries.length);
  $("#nav-count").textContent = number(data.entries.length);
  $("#metric-entries").textContent = number(data.entries.length);
  $("#metric-checks").textContent = number(checks.size);
  $("#metric-copies").textContent = number(data.rehearsals.length);
  syncSelection();
}

function renderPicker() {
  const picker = $("#compose-picker");
  picker.replaceChildren();
  if (!data.entries.length) picker.append(element("p", "muted form-footnote", "候选库为空，先构建或登记一份候选。"));
  data.entries.forEach(entry => {
    const row = element("label", "pick-row");
    const text = element("div");
    text.append(element("strong", "", entry.title), element("span", "", entry.id + " · " + number(entry.changed_values) + " 项修改"));
    row.append(selectionCheckbox(entry), text);
    picker.append(row);
  });
  const select = $("#rehearse-select");
  const old = select.value;
  select.replaceChildren(element("option", "", "选择一个候选"));
  select.options[0].value = "";
  data.entries.forEach(entry => {
    const option = element("option", "", entry.title + " · " + entry.id);
    option.value = entry.id;
    select.append(option);
  });
  if (data.entries.some(entry => entry.id === old)) select.value = old;
}

function renderPreview() {
  const box = $("#preview-result");
  const result = currentPreview();
  box.replaceChildren();
  box.classList.toggle("hidden", !result);
  if (!result) { updateControls(); return; }
  box.classList.toggle("failed", !result.plans_mergeable);
  if (result.plans_mergeable) {
    box.append(element("h3", "", "✓ 修改计划可合并"),
      element("div", "", number(result.merged_assets) + " 份资产 · " + number(result.merged_edits) + " 项编辑"),
      element("div", "", Object.keys(result.shared_output_paths).length + " 个共同输出路径将在编译时统一重建。"),
      element("div", "", "计划检查通过；完整字节与类型检查会在重新编译时执行。"));
  } else {
    box.append(element("h3", "", "存在修改冲突"), element("div", "", result.conflict));
  }
  updateControls();
}

function renderCopies() {
  const list = $("#copy-list");
  list.replaceChildren();
  $("#copy-empty").classList.toggle("hidden", data.rehearsals.length > 0);
  data.rehearsals.forEach(copy => {
    const row = element("article", "copy-row");
    const info = element("div");
    const heading = element("div", "copy-title");
    const verified = copyChecks.has(copy.id);
    heading.append(element("h3", "", copy.id), element("span", "badge " + (copy.state === "restored" ? "ok" : "neutral"), states[copy.state] || copy.state));
    const title = data.entries.find(entry => entry.id === copy.entry_id)?.title || copy.entry_id;
    info.append(heading, element("div", "copy-info", title + "\n" + (verified ? "本次会话已核对备份与目标文件" : "日志快照 · 当前文件尚未重新核对")));
    const stats = element("div", "copy-stats");
    stats.append(element("div", "", copy.baseline_files + " 份原始文件"), element("div", "", copy.modified_files + " 个覆盖 / 新增路径"));
    const actions = element("div", "button-row");
    const restore = button(copy.state === "restored" ? "已还原" : "还原副本", "restore restore-button", () => runAction({ action: "restore", run: copy.id }));
    restore.dataset.restored = String(copy.state === "restored");
    actions.append(button("核对状态", "outline status-button", () => runAction({ action: "status", run: copy.id })), restore,
      button("复制目录", "subtle", () => copyText(copy.path)));
    row.append(info, stats, actions);
    list.append(row);
  });
  updateControls();
}

function renderSettings() {
  const workspace = data.workspace;
  $("#setting-root").textContent = workspace.root;
  $("#setting-config").textContent = workspace.config || "尚未初始化";
  $("#setting-game").textContent = workspace.game_root || "从本机流水线配置读取";
  $("#setting-time").textContent = new Date(workspace.snapshot_at).toLocaleString();
  $("#init-panel").classList.toggle("hidden", workspace.ready);
  $("#ready-panel").classList.toggle("hidden", !workspace.ready);
  const config = $("#init-form [name=config]");
  if (!config.value) config.value = workspace.config_hint;
  const moduleSelect = $("#module-select");
  const old = moduleSelect.value;
  moduleSelect.replaceChildren();
  workspace.modules.forEach(module => {
    const option = element("option", "", module.title);
    option.value = module.id;
    moduleSelect.append(option);
  });
  if (workspace.modules.some(module => module.id === old)) moduleSelect.value = old;
  const alert = $("#workspace-alert");
  alert.replaceChildren();
  const message = workspace.error || (!workspace.ready ? "工作区尚未初始化，先连接项目 local 中的本机配置。" : workspace.build_error);
  alert.classList.toggle("hidden", !message);
  alert.classList.toggle("error", Boolean(workspace.error));
  if (message) alert.append(element("span", "", message), button("查看设置 →", "subtle", () => go("settings")));
  const warnings = $("#warning-area");
  warnings.replaceChildren(...data.warnings.map(message => element("div", "notice warning", message)));
  updateControls();
}

function openDetail(id) {
  const entry = data.details[id];
  if (!entry) return;
  detailsId = id;
  $("#detail-title").textContent = entry.title;
  const content = $("#detail-content");
  content.replaceChildren(element("div", "candidate-id", entry.id));
  const stats = element("div", "detail-stats");
  stats.append(statBlock(entry.stats.assets_built, "资产"), statBlock(entry.stats.changed_values, "数值修改"), statBlock(entry.stats.changed_bytes, "变更字节"));
  content.append(stats, element("div", "form-note", "离线实验候选。封装接受性、游戏加载与比赛效果尚未验证。"));
  const fields = element("div", "detail-block");
  fields.append(element("h3", "", "资产与修改字段"));
  Object.entries(entry.assets_fields).forEach(([name, names]) => {
    const details = element("details");
    const label = name.split("/").pop();
    details.append(element("summary", "", label + " · " + names.length + " 项编辑路径"));
    const items = element("ul");
    names.forEach(field => items.append(element("li", "", field)));
    details.append(items);
    fields.append(details);
  });
  const technical = element("div", "detail-block");
  const details = element("details");
  details.append(element("summary", "", "查看来源、版本绑定与输出目录"), element("pre", "", JSON.stringify({ origin: entry.origin, paths: entry.paths, binding: entry.binding, unresolved: entry.unresolved }, null, 2)));
  technical.append(details);
  content.append(fields, technical);
  $("#detail-dialog").showModal();
  updateControls();
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); showToast("已复制目录路径"); }
  catch { showToast("浏览器不允许复制，请从工作区设置或任务记录查看路径。", true); }
}

function jobSummary(job) {
  const result = job.result;
  if (job.error) return job.error;
  if (!result) return job.phase;
  if (job.action === "check") return "离线预检通过：" + number(result.assets_built) + " 份资产、" + number(result.changed_values) + " 项修改；检查 " + number(result.bundle_locations_checked) + " 个位置。";
  if (["build", "compose", "register"].includes(job.action)) return "已登记「" + result.title + "」：" + number(result.assets_built) + " 份资产、" + number(result.changed_values) + " 项修改、" + number(result.changed_bytes) + " 个变更字节。";
  if (job.action === "preview") return result.plans_mergeable ? "修改计划可合并；" + Object.keys(result.shared_output_paths).length + " 个共同输出路径将统一重建。" : "计划冲突：" + result.conflict;
  if (job.action === "rehearse") return "副本已应用：" + result.baseline_files + " 份原始文件 → " + result.applied_files + " 份候选文件，" + result.modified_files + " 个覆盖 / 新增路径。";
  if (job.action === "restore") return "副本已还原，全部目标文件与应用前基线一致。";
  if (job.action === "status") return "当前状态：" + (states[result.state] || result.state) + "。备份和目标文件核对通过。";
  return job.action === "init" ? "工作区已初始化，可以构建或登记候选。" : "工作区快照已刷新。";
}

function updateJobFacts() {
  for (const job of data.jobs) {
    if (job.action === "check") {
      if (job.state === "succeeded") checks.set(job.subject, job.finished_at);
      else checks.delete(job.subject);
    }
    if (["status", "restore", "rehearse"].includes(job.action) && job.state === "succeeded") {
      const id = job.result.run;
      copyChecks.set(id, job.finished_at);
    }
    if (["status", "restore"].includes(job.action) && job.state === "failed") copyChecks.delete(job.subject);
    if (job.action === "preview" && job.state === "succeeded") {
      const key = job.selected.slice().sort().join("|");
      if (key === selectionKey()) preview = { key, result: job.result };
    }
  }
}

function renderJobs() {
  updateJobFacts();
  const jobs = data.jobs;
  const active = jobs.find(job => job.state === "running");
  const last = jobs.at(-1);
  $("#task-title").textContent = active ? active.label : "本次会话任务";
  $("#task-summary").textContent = active ? "后台执行中，页面可以继续浏览" : (jobs.length ? jobs.length + " 项任务 · " + (last.state === "failed" ? "最近一次失败" : "最近一次完成") : "尚无任务");
  $("#task-indicator").classList.toggle("busy", Boolean(active));
  $("#task-indicator").classList.toggle("failed", last?.state === "failed");
  $("#task-live").classList.toggle("hidden", !active);
  if (active) $("#task-phase").textContent = active.phase;
  const history = $("#task-history");
  history.replaceChildren();
  jobs.slice().reverse().forEach(job => {
    const record = element("article", "job-record");
    const top = element("div", "job-top");
    const text = job.state === "running" ? "进行中" : job.state === "failed" ? "失败" : "完成";
    top.append(element("strong", "", job.label + (job.subject ? " · " + job.subject : "")), element("span", "badge " + (job.state === "succeeded" ? "ok" : "neutral"), text));
    record.append(top, element("div", "job-summary" + (job.error ? " error" : ""), jobSummary(job)));
    if (job.elapsed_seconds !== null) record.append(element("div", "form-footnote", new Date(job.finished_at).toLocaleTimeString() + " · 耗时 " + job.elapsed_seconds + " 秒"));
    const details = element("details");
    details.append(element("summary", "", "任务记录与完整结果"), element("pre", "", JSON.stringify({ events: job.events, result: job.result, error: job.error }, null, 2)));
    record.append(details);
    history.append(record);
    if (job.state !== "running" && !finishedJobs.has(job.id)) {
      finishedJobs.add(job.id);
      if (job.state === "failed") {
        historyOpen = true;
        showToast(job.label + "失败：" + job.error, true);
      } else if (job.action !== "refresh") showToast(jobSummary(job));
    }
  });
  setHistoryVisible();
  updateClock();
  renderLibrary();
  renderCopies();
  renderPreview();
}

function setHistoryVisible() {
  $("#task-history").classList.toggle("hidden", !historyOpen);
  $("#toggle-tasks").textContent = historyOpen ? "收起记录 ↑" : "展开记录 ↓";
  $("#toggle-tasks").setAttribute("aria-expanded", String(historyOpen));
}

function updateClock() {
  const active = data?.jobs.find(job => job.state === "running");
  if (active) {
    const elapsed = Math.max(0, Math.floor((Date.now() - new Date(active.started_at).getTime()) / 1000));
    $("#task-elapsed").textContent = elapsed + " 秒";
  }
}

async function loadState() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error("本机服务暂时无法读取状态");
    const value = await response.json();
    if (!loadedOnce) {
      value.jobs.filter(job => job.state !== "running").forEach(job => finishedJobs.add(job.id));
      loadedOnce = true;
    }
    const wasConnected = connected;
    connected = true;
    const nextKey = JSON.stringify([value.workspace, value.entries, value.rehearsals, value.warnings]);
    const nextJobs = JSON.stringify(value.jobs);
    data = value;
    $("#connection-dot").classList.remove("offline");
    $("#connection-text").textContent = value.closing ? "当前任务完成后退出" : "本机服务已连接";
    if (nextKey !== snapshotKey || !wasConnected) {
      snapshotKey = nextKey;
      selected = new Set(Array.from(selected).filter(id => value.entries.some(entry => entry.id === id)));
      updateJobFacts();
      renderLibrary(); renderPicker(); renderCopies(); renderSettings(); renderPreview();
    }
    if (nextJobs !== jobsKey) { jobsKey = nextJobs; renderJobs(); }
    updateControls();
  } catch (error) {
    connected = false;
    $("#connection-dot").classList.add("offline");
    $("#connection-text").textContent = "本机服务连接中断";
    updateControls();
    const alert = $("#workspace-alert");
    alert.replaceChildren(element("span", "", "本机服务连接中断。请确认管理器服务仍在运行，页面会自动重新连接。"));
    alert.classList.remove("hidden");
    alert.classList.add("error");
  }
}

async function runAction(payload) {
  if (busy()) { showToast("请等待当前任务完成", true); return false; }
  sending = true;
  updateControls();
  try {
    const response = await fetch("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json", "X-FC27-Token": data.token }, body: JSON.stringify(payload) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "任务提交失败");
    if (payload.action === "preview") { preview = null; renderPreview(); }
    if (payload.action === "check") checks.delete(payload.id);
    showToast("任务已开始，可在下方查看进度与结果。");
    await loadState();
    return true;
  } catch (error) { showToast(error.message, true); return false; }
  finally { sending = false; updateControls(); }
}

function formPayload(form) {
  return Object.fromEntries(Array.from(new FormData(form), ([key, value]) => [key, String(value).trim()]));
}

function bindForm(id, action, extra = () => ({}), after = () => {}) {
  $(id).addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const accepted = await runAction({ action, ...formPayload(form), ...extra() });
    if (accepted) after();
  });
}

$$("[data-page]").forEach(node => node.addEventListener("click", () => go(node.dataset.page)));
$$("[data-go]").forEach(node => node.addEventListener("click", () => go(node.dataset.go)));
window.addEventListener("hashchange", () => go(location.hash.slice(1)));
$("#search").addEventListener("input", () => { if (data) renderLibrary(); });
$("#refresh-button").addEventListener("click", () => runAction({ action: "refresh" }));
$("#exit-button").addEventListener("click", async () => {
  try {
    const response = await fetch("/api/exit", { method: "POST", headers: { "Content-Type": "application/json", "X-FC27-Token": data.token }, body: "{}" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "退出失败");
    data.closing = true;
    $("#connection-text").textContent = "当前任务完成后退出";
    updateControls();
  } catch (error) { showToast(error.message, true); }
});
$("#clear-selection").addEventListener("click", () => { selected.clear(); preview = null; syncSelection(); renderPreview(); });
$("#selection-preview").addEventListener("click", () => { go("build"); runAction({ action: "preview", ids: Array.from(selected) }); });
$("#selection-compose").addEventListener("click", () => go("build"));
$("#compose-preview").addEventListener("click", () => runAction({ action: "preview", ids: Array.from(selected) }));
$("#open-register").addEventListener("click", () => $("#register-dialog").showModal());
$$(".close-dialog").forEach(node => node.addEventListener("click", () => node.closest("dialog").close()));
$("#detail-check").addEventListener("click", async () => { if (await runAction({ action: "check", id: detailsId })) $("#detail-dialog").close(); });
$("#detail-rehearse").addEventListener("click", () => { $("#detail-dialog").close(); go("copies"); $("#rehearse-select").value = detailsId; $("#rehearse-form [name=run]").focus(); });
$("#toggle-tasks").addEventListener("click", () => { historyOpen = !historyOpen; setHistoryVisible(); });
$$(".generate-id").forEach(node => node.addEventListener("click", () => {
  const input = node.parentElement.querySelector("input");
  input.value = node.dataset.prefix + "-" + new Date().toISOString().replace(/\D/g, "").slice(0, 14) + "-" + crypto.getRandomValues(new Uint8Array(2)).reduce((result, byte) => result + byte.toString(16).padStart(2, "0"), "");
  input.dispatchEvent(new Event("input", { bubbles: true }));
}));
bindForm("#build-form", "build");
bindForm("#compose-form", "compose", () => ({ ids: Array.from(selected) }));
bindForm("#rehearse-form", "rehearse");
bindForm("#init-form", "init");
bindForm("#register-form", "register", () => ({}), () => $("#register-dialog").close());
go(location.hash.slice(1));
(async function poll() { await loadState(); setTimeout(poll, 1500); })();
setInterval(updateClock, 1000);
