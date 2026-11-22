const MAX_UPLOAD_SIZE = 20 * 1024 * 1024;
const MAX_FILES = 100;
const CATEGORY_RULES = {
  Images: ["jpg","jpeg","png","gif","webp","svg","bmp","tif","tiff","heic"],
  Documents: ["pdf","doc","docx","odt","rtf","txt","md"],
  Spreadsheets: ["csv","xls","xlsx","ods"],
  Presentations: ["ppt","pptx","odp","key"],
  Videos: ["mp4","mkv","mov","avi","webm"],
  Audio: ["mp3","wav","aac","ogg","flac","m4a"],
  Archives: ["zip","rar","7z","tar","gz"],
  Code: ["py","js","ts","html","css","json","java","c","cpp","cs","go","rs","php","rb","sh","sql","xml","yaml","yml","toml"]
};

let queuedFiles = [];
let organizedFiles = [];
let stats = null;
let toastTimer;
let previewReady = false;

const $ = (id) => document.getElementById(id);

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1073741824) return `${(bytes / 1048576).toFixed(1)} MB`;
  return `${(bytes / 1073741824).toFixed(1)} GB`;
}

function extensionOf(name) {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
}

function categoryFor(name) {
  const extension = extensionOf(name);
  for (const [category, extensions] of Object.entries(CATEGORY_RULES)) {
    if (extensions.includes(extension)) return category;
  }
  return "Other";
}

function fileIcon(name) {
  const category = categoryFor(name);
  return { Images:"▧", Documents:"▤", Spreadsheets:"⊞", Presentations:"▰", Videos:"▶", Audio:"♫", Archives:"▦", Code:"</>", Other:"•" }[category] || "•";
}

function showToast(message) {
  const toast = $("toast");
  toast.textContent = message;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 2600);
}

function navigate(page) {
  document.querySelectorAll(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.page === page));
  document.querySelectorAll("[data-page-panel]").forEach(panel => panel.classList.toggle("active", panel.dataset.pagePanel === page));
  const labels = { dashboard:"Dashboard", explorer:"File Explorer", organize:"Organize Files", categories:"Categories", history:"Activity History", analytics:"Analytics", settings:"Settings" };
  $("pageTitle").textContent = labels[page] || "Dashboard";
  document.body.dataset.page = page;
  $("sidebar").classList.remove("open");
  window.scrollTo({ top: 0, behavior: "smooth" });
  if (page === "explorer") renderExplorer();
  if (page === "categories") renderCategories();
  if (page === "history") renderHistory();
  if (page === "analytics") renderAnalytics();
}

async function loadStats() {
  try {
    const response = await fetch("/api/stats");
    if (!response.ok) throw new Error("Unable to load dashboard data");
    stats = await response.json();
    renderDashboard(stats);
  } catch (error) {
    showToast(error.message);
  }
}

async function loadFiles() {
  try {
    const response = await fetch("/api/files");
    if (!response.ok) throw new Error("Unable to load files");
    const data = await response.json();
    organizedFiles = data.files || [];
    renderExplorer();
  } catch (error) {
    showToast(error.message);
  }
}

function setText(id, value) { $(id).textContent = value; }

function renderDashboard(data) {
  setText("statTotal", data.total_files.toLocaleString());
  setText("statOrganized", data.organized_files.toLocaleString());
  setText("statStorage", data.storage_used_label);
  setText("statCategories", data.categories.toLocaleString());
  setText("analyticsTotal", data.total_files.toLocaleString());
  setText("analyticsOrganized", data.organized_files.toLocaleString());
  setText("analyticsStorage", data.storage_used_label);
  setText("analyticsCategories", data.categories.toLocaleString());

  const chart = $("categoryChart");
  const counts = data.category_counts || {};
  chart.replaceChildren();
  const maxCount = Math.max(1, ...Object.values(counts));
  if (!Object.keys(counts).length) chart.innerHTML = '<p class="muted">No organized files yet.</p>';
  Object.entries(counts).sort((a,b) => b[1]-a[1]).forEach(([category,count]) => {
    const row = document.createElement("div");
    row.className = "bar-row";
    row.innerHTML = `<span>${category}</span><div class="bar-track"><div class="bar-fill" style="width:${count/maxCount*100}%"></div></div><strong>${count}</strong>`;
    chart.appendChild(row);
  });

  const organizedRatio = data.total_files ? Math.round(data.organized_files / Math.max(data.total_files, data.organized_files) * 100) : 0;
  setText("ringValue", `${organizedRatio}%`);
  $("storageRing").style.setProperty("--ring", `${organizedRatio}%`);
  $("storageLegend").innerHTML = `<span>${data.organized_files} organized</span><span>${data.total_files} uploaded</span>`;

  const activity = $("recentActivity");
  activity.replaceChildren();
  if (!data.history?.length) activity.innerHTML = '<p class="muted">No organization activity yet.</p>';
  data.history.slice(0,5).forEach(run => {
    const row = document.createElement("div");
    row.className = "activity-row";
    row.innerHTML = `<span class="dot"></span><div><strong>${run.files_organized} files organized</strong><small>${new Date(run.date).toLocaleString()}</small></div>`;
    activity.appendChild(row);
  });

  const recent = $("recentFiles");
  recent.replaceChildren();
  if (!data.recent_organized?.length) recent.innerHTML = '<p class="muted">No organized files yet.</p>';
  data.recent_organized.slice(0,5).forEach(file => {
    const row = document.createElement("div");
    row.className = "recent-row";
    row.innerHTML = `<span class="file-mini-icon">${fileIcon(file.filename)}</span><div><strong title="${file.filename}">${file.filename}</strong><small>${file.category}</small></div><span class="size">${file.size_label}</span>`;
    recent.appendChild(row);
  });
  renderAnalytics();
}

function renderExplorer() {
  const search = $("explorerSearch")?.value.trim().toLowerCase() || "";
  const category = $("explorerCategory")?.value || "all";
  const sort = $("explorerSort")?.value || "date-desc";
  let files = organizedFiles.filter(file =>
    (!search || file.filename.toLowerCase().includes(search)) &&
    (category === "all" || file.category === category)
  );
  files.sort((a,b) => {
    if (sort === "name") return a.filename.localeCompare(b.filename);
    if (sort === "size-desc") return b.size-a.size;
    if (sort === "size-asc") return a.size-b.size;
    return new Date(b.modified)-new Date(a.modified);
  });

  const categorySelect = $("explorerCategory");
  if (categorySelect && !categorySelect.dataset.loaded) {
    [...new Set(organizedFiles.map(file => file.category))].sort().forEach(name => categorySelect.add(new Option(name,name)));
    categorySelect.dataset.loaded = "true";
  }

  const grid = $("fileExplorer");
  grid.replaceChildren();
  if (!files.length) {
    grid.innerHTML = '<div class="empty-state"><div><strong>No files found</strong><p>Upload and organize files to populate this space.</p></div></div>';
    return;
  }
  files.forEach(file => {
    const tile = document.createElement("article");
    tile.className = "file-tile";
    const preview = file.previewable
      ? `<img src="/preview/${encodeURI(file.relative_path)}" alt="" loading="lazy">`
      : fileIcon(file.filename);
    tile.innerHTML = `<div class="file-preview">${preview}</div><strong title="${file.filename}">${file.filename}</strong><small>${file.category} · ${file.size_label}</small>`;
    grid.appendChild(tile);
  });
}

function renderCategories() {
  const container = $("categoryCards");
  container.replaceChildren();
  const counts = stats?.category_counts || {};
  const categories = ["Images","Documents","Spreadsheets","Presentations","Videos","Audio","Archives","Code","Other"];
  categories.forEach(category => {
    const count = counts[category] || 0;
    const card = document.createElement("article");
    card.className = "category-card";
    card.innerHTML = `<div class="category-card-head"><span class="category-card-icon">${categoryIcon(category)}</span><strong>${category}</strong></div><p>Organized files</p><strong>${count}</strong>`;
    container.appendChild(card);
  });
}

function categoryIcon(category) {
  return { Images:"▧", Documents:"▤", Spreadsheets:"⊞", Presentations:"▰", Videos:"▶", Audio:"♫", Archives:"▦", Code:"</>", Other:"•" }[category];
}

function renderHistory() {
  const container = $("historyList");
  container.replaceChildren();
  const history = stats?.history || [];
  if (!history.length) { container.innerHTML = '<p class="muted">No organization sessions recorded.</p>'; return; }
  history.forEach(run => {
    const row = document.createElement("article");
    row.className = "history-row";
    row.innerHTML = `<span class="history-status">✓</span><div><strong>${run.files_organized} files organized</strong><p>${run.filenames?.join(", ") || run.categories_created?.join(", ") || "Organization completed"}</p></div><time>${new Date(run.date).toLocaleString()}</time>`;
    container.appendChild(row);
  });
}

function renderAnalytics() {
  if (!stats) return;
  const storage = $("storageChart");
  const types = $("typeChart");
  storage.replaceChildren(); types.replaceChildren();
  const sizes = stats.category_sizes || {};
  const maxSize = Math.max(1, ...Object.values(sizes));
  Object.entries(sizes).sort((a,b)=>b[1]-a[1]).forEach(([category,size]) => {
    storage.innerHTML += `<div class="chart-row"><span>${category}</span><div class="bar-track"><div class="bar-fill" style="width:${size/maxSize*100}%"></div></div><strong>${formatBytes(size)}</strong></div>`;
  });
  if (!Object.keys(sizes).length) storage.innerHTML = '<p class="muted">No storage data yet.</p>';
  const extensions = stats.extension_counts || {};
  const maxTypes = Math.max(1, ...Object.values(extensions));
  Object.entries(extensions).forEach(([extension,count]) => {
    types.innerHTML += `<div class="chart-row"><span>${extension}</span><div class="bar-track"><div class="bar-fill" style="width:${count/maxTypes*100}%"></div></div><strong>${count}</strong></div>`;
  });
  if (!Object.keys(extensions).length) types.innerHTML = '<p class="muted">No file type data yet.</p>';
}

function updateQueue() {
  $("queueSection").hidden = !queuedFiles.length;
  $("organizeBtn").disabled = !queuedFiles.length;
  $("queueTitle").textContent = `${queuedFiles.length} file${queuedFiles.length === 1 ? "" : "s"} · ${formatBytes(queuedFiles.reduce((sum,file)=>sum+file.size,0))}`;
  const queue = $("fileQueue");
  queue.replaceChildren();
  queuedFiles.forEach((file,index) => {
    const row = document.createElement("div");
    row.className = "queue-file";
    row.innerHTML = `<span class="file-mini-icon">${fileIcon(file.name)}</span><div><strong>${file.name}</strong><small>${formatBytes(file.size)} · ${categoryFor(file.name)}</small></div><button class="remove-file" type="button" aria-label="Remove ${file.name}">×</button>`;
    row.querySelector("button").addEventListener("click", () => { queuedFiles.splice(index,1); updateQueue(); renderCategoryPreview(); });
    queue.appendChild(row);
  });
  renderCategoryPreview();
  previewReady = false;
  $("organizationPreview").hidden = true;
}

function addFiles(files) {
  let remaining = MAX_UPLOAD_SIZE - queuedFiles.reduce((sum,file)=>sum+file.size,0);
  let rejected = [];
  Array.from(files).forEach(file => {
    if (queuedFiles.length >= MAX_FILES) { rejected.push("The 100-file limit was reached"); return; }
    if (file.size > remaining) { rejected.push(`${file.name} exceeds the remaining upload size`); return; }
    queuedFiles.push(file); remaining -= file.size;
  });
  updateQueue();
  if (rejected.length) showToast(rejected[0]);
  else if (files.length) showToast(`${files.length} file${files.length === 1 ? "" : "s"} added to the queue`);
}

function renderCategoryPreview() {
  const container = $("categoryPreview");
  container.replaceChildren();
  if (!queuedFiles.length) { container.innerHTML = '<p class="muted">Add files to preview their categories.</p>'; return; }
  const counts = {};
  queuedFiles.forEach(file => { const category = categoryFor(file.name); counts[category]=(counts[category]||0)+1; });
  Object.entries(counts).sort((a,b)=>b[1]-a[1]).forEach(([category,count]) => {
    const row = document.createElement("div");
    row.className = "preview-category-row";
    row.innerHTML = `<span>${categoryIcon(category)}</span><strong>${category}</strong><small>${count} file${count===1?"":"s"}</small>`;
    container.appendChild(row);
  });
}

function previewOrganization() {
  if (!queuedFiles.length) return;
  const counts = {};
  queuedFiles.forEach(file => { const category = categoryFor(file.name); counts[category]=(counts[category]||0)+1; });
  const container = $("previewFiles");
  container.replaceChildren();
  queuedFiles.forEach(file => {
    const row = document.createElement("div");
    row.className = "preview-file";
    row.innerHTML = `<span class="file-mini-icon">${fileIcon(file.name)}</span><div><strong>${file.name}</strong><small>${categoryFor(file.name)} · ${formatBytes(file.size)}</small></div>`;
    container.appendChild(row);
  });
  setText("previewSummary", `${queuedFiles.length} files · ${Object.keys(counts).length} categories`);
  $("organizationPreview").hidden = false;
  previewReady = true;
  $("organizationPreview").scrollIntoView({ behavior:"smooth", block:"center" });
}

async function applyOrganization() {
  if (!previewReady || !queuedFiles.length) return;
  const form = new FormData();
  queuedFiles.forEach(file => form.append("files", file));
  form.append("skip_duplicates", String($("skipDuplicates").checked));
  $("progressPanel").hidden = false;
  $("progressFill").style.width = "8%";
  $("progressPercent").textContent = "8%";
  $("progressText").textContent = "Uploading files…";

  const request = new XMLHttpRequest();
  request.open("POST","/organize");
  request.timeout = 60000;
  request.upload.onprogress = event => {
    if (!event.lengthComputable) return;
    const percent = Math.round(event.loaded/event.total*100);
    $("progressFill").style.width = `${percent}%`;
    $("progressPercent").textContent = `${percent}%`;
  };
  request.onload = () => {
    let data;
    try { data = JSON.parse(request.responseText); } catch {
      $("progressPanel").hidden = true;
      showToast("The server returned an unreadable response.");
      return;
    }
    if (request.status >= 200 && request.status < 300) {
      $("progressFill").style.width = "100%";
      $("progressPercent").textContent = "100%";
      $("progressText").textContent = "Organization complete";
      showToast(data.message || "Files organized successfully");
      queuedFiles = [];
      updateQueue();
      $("organizationPreview").hidden = true;
      Promise.all([loadStats(), loadFiles()]);
      setTimeout(() => { $("progressPanel").hidden = true; $("progressFill").style.width = "0%"; }, 900);
      return;
    }
    $("progressPanel").hidden = true;
    showToast(data.error || "Unable to organize files");
  };
  request.onerror = () => {
    $("progressPanel").hidden = true;
    showToast("Could not reach the server");
  };
  request.ontimeout = () => {
    $("progressPanel").hidden = true;
    showToast("The upload took too long. Please try again.");
  };
  request.onabort = () => {
    $("progressPanel").hidden = true;
  };
  request.send(form);
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("fileflow-theme", theme);
  $("themeSetting").checked = theme === "light";
  $("themeToggle").textContent = theme === "light" ? "☾" : "☼";
}

document.querySelectorAll(".nav-item").forEach(item => item.addEventListener("click", () => navigate(item.dataset.page)));
document.querySelectorAll("[data-go]").forEach(button => button.addEventListener("click", () => navigate(button.dataset.go)));
$("sidebarCollapse").addEventListener("click", () => $("sidebar").classList.toggle("collapsed"));
$("mobileMenu").addEventListener("click", () => $("sidebar").classList.add("open"));
$("sidebarBackdrop").addEventListener("click", () => $("sidebar").classList.remove("open"));
$("themeToggle").addEventListener("click", () => applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light"));
$("themeSetting").addEventListener("change", event => applyTheme(event.target.checked ? "light" : "dark"));
$("themeSetting").checked = false;

["dragenter","dragover"].forEach(name => $("dropZone").addEventListener(name, event => { event.preventDefault(); $("dropZone").classList.add("drag-over"); }));
$("dropZone").addEventListener("dragleave", () => $("dropZone").classList.remove("drag-over"));
$("dropZone").addEventListener("drop", event => { event.preventDefault(); $("dropZone").classList.remove("drag-over"); addFiles(event.dataTransfer.files); });
$("dropZone").addEventListener("keydown", event => { if (event.key==="Enter" || event.key===" ") $("fileInput").click(); });
$("fileInput").addEventListener("change", event => { addFiles(event.target.files); event.target.value = ""; });
$("folderInput").addEventListener("change", event => { addFiles(event.target.files); event.target.value = ""; });
$("clearQueueBtn").addEventListener("click", () => { queuedFiles=[]; updateQueue(); });
$("organizeBtn").addEventListener("click", previewOrganization);
$("cancelPreviewBtn").addEventListener("click", () => { previewReady=false; $("organizationPreview").hidden=true; });
$("applyOrganizationBtn").addEventListener("click", applyOrganization);

$("explorerSearch").addEventListener("input", renderExplorer);
$("explorerCategory").addEventListener("change", renderExplorer);
$("explorerSort").addEventListener("change", renderExplorer);
$("gridViewBtn").addEventListener("click", () => { $("fileExplorer").classList.remove("list"); $("gridViewBtn").classList.add("active"); $("listViewBtn").classList.remove("active"); });
$("listViewBtn").addEventListener("click", () => { $("fileExplorer").classList.add("list"); $("listViewBtn").classList.add("active"); $("gridViewBtn").classList.remove("active"); });

$("duplicateSetting").addEventListener("change", event => { localStorage.setItem("fileflow-duplicates", String(event.target.checked)); $("skipDuplicates").checked=event.target.checked; });
$("viewSetting").addEventListener("change", event => { localStorage.setItem("fileflow-grid-view", String(event.target.checked)); if(event.target.checked) $("gridViewBtn").click(); else $("listViewBtn").click(); });
$("resetSettingsBtn").addEventListener("click", () => {
  localStorage.removeItem("fileflow-theme"); localStorage.removeItem("fileflow-duplicates"); localStorage.removeItem("fileflow-grid-view");
  applyTheme("dark"); $("duplicateSetting").checked=false; $("viewSetting").checked=true; $("skipDuplicates").checked=false; showToast("Settings reset");
});

window.addEventListener("scroll", () => $("backToTop").classList.toggle("show", window.scrollY > 400));
$("backToTop").addEventListener("click", () => window.scrollTo({top:0,behavior:"smooth"}));

const savedTheme = localStorage.getItem("fileflow-theme") || "dark";
applyTheme(savedTheme);
const duplicatePreference = localStorage.getItem("fileflow-duplicates") === "true";
$("duplicateSetting").checked = duplicatePreference;
$("skipDuplicates").checked = duplicatePreference;
const gridPreference = localStorage.getItem("fileflow-grid-view");
$("viewSetting").checked = gridPreference !== "false";

Promise.all([loadStats(), loadFiles()]);
