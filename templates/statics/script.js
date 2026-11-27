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
let selectedFiles = new Set();
let lastPreviewFile = null;
let confirmAction = null;
let folders = [];
let activeFileFilter = "all";

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

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, character => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;"
  })[character]);
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
  activeFileFilter = page === "favorites" || page === "recent" ? page : "all";
  const activePanel = activeFileFilter === "all" ? page : "explorer";
  document.querySelectorAll(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.page === page));
  document.querySelectorAll("[data-page-panel]").forEach(panel => panel.classList.toggle("active", panel.dataset.pagePanel === activePanel));
  const labels = { dashboard:"Dashboard", explorer:"File Explorer", recent:"Recently opened", favorites:"Favorites", folders:"My folders", trash:"Trash", organize:"Organize Files", categories:"Categories", history:"Activity History", analytics:"Analytics", settings:"Settings" };
  $("pageTitle").textContent = labels[page] || "Dashboard";
  document.body.dataset.page = page;
  $("sidebar").classList.remove("open");
  window.scrollTo({ top: 0, behavior: "smooth" });
  if (activePanel === "explorer") renderExplorer();
  if (page === "categories") renderCategories();
  if (page === "history") renderHistory();
  if (page === "analytics") renderAnalytics();
  if (page === "folders") loadFolders();
  if (page === "trash") loadTrash();
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
    populateExplorerFilters();
    selectedFiles = new Set([...selectedFiles].filter(path => organizedFiles.some(file => file.relative_path === path)));
    renderExplorer();
  } catch (error) {
    showToast(error.message);
  }
}

async function loadFolders() {
  try {
    const response = await fetch("/api/folders");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Unable to load folders");
    folders = data.folders || [];
    renderFolders();
  } catch (error) {
    showToast(error.message);
  }
}

async function loadTrash() {
  try {
    const response = await fetch("/api/trash");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Unable to load trash");
    renderTrash(data.files || []);
  } catch (error) {
    showToast(error.message);
  }
}

function setText(id, value) { $(id).textContent = value; }

function animateCount(id, target, duration = 600) {
  const element = $(id);
  if (!element) return;
  const numeric = Number.parseInt(String(target).replace(/[^0-9]/g, ""), 10) || 0;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    element.textContent = target;
    return;
  }
  const start = performance.now();
  function update(now) {
    const progress = Math.min((now - start) / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    element.textContent = Math.round(numeric * eased).toLocaleString();
    if (progress < 1) requestAnimationFrame(update);
  }
  requestAnimationFrame(update);
}

function renderDashboard(data) {
  animateCount("statTotal", data.total_files);
  animateCount("statOrganized", data.favorites || 0);
  setText("statStorage", data.storage_used_label);
  setText("statCategories", (data.folders || 0).toLocaleString());
  setText("analyticsTotal", data.total_files.toLocaleString());
  setText("analyticsOrganized", (data.favorites || 0).toLocaleString());
  setText("analyticsStorage", data.storage_used_label);
  setText("analyticsCategories", (data.folders || 0).toLocaleString());

  const chart = $("categoryChart");
  const counts = data.category_counts || {};
  chart.replaceChildren();
  const maxCount = Math.max(1, ...Object.values(counts));
  if (!Object.keys(counts).length) chart.innerHTML = '<p class="muted">No organized files yet.</p>';
  Object.entries(counts).sort((a,b) => b[1]-a[1]).forEach(([category,count]) => {
    const row = document.createElement("div");
    row.className = "bar-row";
    row.innerHTML = `<span>${escapeHtml(category)}</span><div class="bar-track"><div class="bar-fill" style="width:${count/maxCount*100}%"></div></div><strong>${count}</strong>`;
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
    row.innerHTML = `<span class="dot"></span><div><strong>${Number(run.files_organized) || 0} files organized</strong><small>${escapeHtml(new Date(run.date).toLocaleString())}</small></div>`;
    activity.appendChild(row);
  });

  const recent = $("recentFiles");
  recent.replaceChildren();
  if (!data.recent_organized?.length) recent.innerHTML = '<p class="muted">No organized files yet.</p>';
  data.recent_organized.slice(0,5).forEach(file => {
    const row = document.createElement("div");
    row.className = "recent-row";
    row.innerHTML = `<span class="file-mini-icon">${fileIcon(file.filename)}</span><div><strong title="${escapeHtml(file.filename)}">${escapeHtml(file.filename)}</strong><small>${escapeHtml(file.category)}</small></div><span class="size">${escapeHtml(file.size_label)}</span>`;
    recent.appendChild(row);
  });
  renderAnalytics();
}

function populateExplorerFilters() {
  const categorySelect = $("explorerCategory");
  const extensionSelect = $("explorerExtension");
  if (categorySelect) {
    const category = categorySelect.value || "all";
    categorySelect.replaceChildren(new Option("All categories", "all"));
    [...new Set(organizedFiles.map(file => file.category))].sort().forEach(name => categorySelect.add(new Option(name, name)));
    if ([...categorySelect.options].some(option => option.value === category)) categorySelect.value = category;
  }
  if (extensionSelect) {
    const current = extensionSelect.value || "all";
    extensionSelect.replaceChildren(new Option("All extensions", "all"));
    [...new Set(organizedFiles.map(file => extensionOf(file.filename)).filter(Boolean))].sort().forEach(name => extensionSelect.add(new Option(`.${name}`, name)));
    if ([...extensionSelect.options].some(option => option.value === current)) extensionSelect.value = current;
  }
}

function updateBulkBar() {
  const visibleCount = selectedFiles.size;
  $("bulkBar").hidden = visibleCount === 0;
  $("selectedCount").textContent = `${visibleCount} selected`;
  const visiblePaths = new Set(organizedFiles.map(file => file.relative_path));
  const visibleSelected = [...selectedFiles].filter(path => visiblePaths.has(path)).length;
  $("selectAllFiles").checked = visibleSelected > 0 && visibleSelected === organizedFiles.length;
  $("selectAllFiles").indeterminate = visibleSelected > 0 && visibleSelected < organizedFiles.length;
}

async function bulkUpdate(action, value = null) {
  if (!selectedFiles.size) return;
  try {
    const response = await fetch("/api/files/bulk", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]')?.content || ""
      },
      body: JSON.stringify({
        action,
        value,
        paths: [...selectedFiles]
      })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not update selected files");
    showToast(data.message);
    await loadFiles();
  } catch (error) {
    showToast(error.message);
  }
}

function openConfirm(title, message, fieldType, callback) {
  $("confirmModalTitle").textContent = title;
  $("confirmModalMessage").textContent = message;
  const field = $("confirmModalField");
  field.replaceChildren();
  if (fieldType === "tag" || fieldType === "collection") {
    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = fieldType === "tag" ? "Tag name" : "Collection name";
    input.maxLength = fieldType === "tag" ? 40 : 50;
    field.appendChild(input);
  }
  confirmAction = callback;
  $("confirmModal").hidden = false;
  const input = field.querySelector("input");
  if (input) setTimeout(() => input.focus(), 0);
}

async function openFilePreview(file) {
  lastPreviewFile = file;
  $("previewModal").hidden = false;
  $("previewModalTitle").textContent = file.filename;
  $("previewModalMeta").innerHTML = `<span>${escapeHtml(file.category)}</span><span>${escapeHtml(file.size_label)}</span><span>${escapeHtml(file.relative_path)}</span>`;
  $("previewFavoriteBtn").textContent = file.favorite ? "★ Favorited" : "☆ Favorite";
  $("previewDownloadBtn").href = `/files/download/${encodeURIComponent(file.relative_path).replace(/%2F/g, "/")}`;
  const body = $("previewModalBody");
  body.replaceChildren();

  if (file.preview_type === "image") {
    const image = document.createElement("img");
    image.src = `/preview/${encodeURIComponent(file.relative_path).replace(/%2F/g, "/")}`;
    image.alt = file.filename;
    body.appendChild(image);
  } else if (file.preview_type === "pdf") {
    const frame = document.createElement("iframe");
    frame.src = `/preview/${encodeURIComponent(file.relative_path).replace(/%2F/g, "/")}`;
    frame.title = file.filename;
    body.appendChild(frame);
  } else if (file.preview_type === "text") {
    try {
      const response = await fetch(`/preview/${encodeURIComponent(file.relative_path).replace(/%2F/g, "/")}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Could not preview this file");
      const pre = document.createElement("pre");
      pre.textContent = data.content;
      body.replaceChildren(pre);
    } catch (error) {
      body.innerHTML = `<div class="preview-empty"><strong>Preview unavailable</strong><p class="muted">${escapeHtml(error.message)}</p></div>`;
    }
  } else {
    body.innerHTML = '<div class="preview-empty"><strong>No inline preview for this file type</strong><p class="muted">Use Open / download to view or save the original file.</p></div>';
  }
}

function renderExplorer() {
  const search = $("explorerSearch")?.value.trim().toLowerCase() || "";
  const category = $("explorerCategory")?.value || "all";
  const extension = $("explorerExtension")?.value || "all";
  const sizeFilter = $("explorerSize")?.value || "all";
  const dateFilter = $("explorerDate")?.value || "all";
  const sort = $("explorerSort")?.value || "date-desc";
  const now = Date.now();
  let files = organizedFiles.filter(file => {
    const fileExtension = extensionOf(file.filename);
    const matchesSearch = !search ||
      file.filename.toLowerCase().includes(search) ||
      file.category.toLowerCase().includes(search) ||
      (file.tags || []).some(tag => tag.toLowerCase().includes(search)) ||
      (file.collection || "").toLowerCase().includes(search);
    const matchesSize =
      sizeFilter === "all" ||
      (sizeFilter === "small" && file.size < 100 * 1024) ||
      (sizeFilter === "medium" && file.size >= 100 * 1024 && file.size <= 5 * 1024 * 1024) ||
      (sizeFilter === "large" && file.size > 5 * 1024 * 1024);
    const age = now - new Date(file.modified).getTime();
    const matchesDate =
      dateFilter === "all" ||
      (dateFilter === "today" && age < 24 * 60 * 60 * 1000) ||
      (dateFilter === "week" && age < 7 * 24 * 60 * 60 * 1000) ||
      (dateFilter === "month" && age < 30 * 24 * 60 * 60 * 1000);
    return matchesSearch &&
      (category === "all" || file.category === category) &&
      (extension === "all" || fileExtension === extension) &&
      matchesSize &&
      matchesDate;
  });
  files.sort((a,b) => {
    if (sort === "name") return a.filename.localeCompare(b.filename);
    if (sort === "size-desc") return b.size-a.size;
    if (sort === "size-asc") return a.size-b.size;
    return new Date(b.modified)-new Date(a.modified);
  });

  const grid = $("fileExplorer");
  grid.replaceChildren();
  if (!files.length) {
    grid.innerHTML = '<div class="empty-state"><div><strong>No files match these filters</strong><p>Clear a filter or upload more files to continue.</p></div></div>';
    updateBulkBar();
    return;
  }
  files.forEach(file => {
    const tile = document.createElement("article");
    tile.className = `file-tile${selectedFiles.has(file.relative_path) ? " selected" : ""}`;
    const preview = file.preview_type === "image"
      ? `<img src="/preview/${encodeURIComponent(file.relative_path).replace(/%2F/g, "/")}" alt="" loading="lazy">`
      : fileIcon(file.filename);
    const tags = (file.tags || []).slice(0, 3).map(tag => `<span class="file-tag">${escapeHtml(tag)}</span>`).join("");
    tile.innerHTML = `
      <label class="file-select" title="Select ${escapeHtml(file.filename)}"><input type="checkbox" ${selectedFiles.has(file.relative_path) ? "checked" : ""}></label>
      ${file.favorite ? '<span class="favorite-badge" title="Favorite">★</span>' : ""}
      <div class="file-preview">${preview}</div>
      <strong title="${escapeHtml(file.filename)}">${escapeHtml(file.filename)}</strong>
      <small>${escapeHtml(file.category)} · ${escapeHtml(file.size_label)}</small>
      ${tags ? `<div class="file-tags">${tags}</div>` : ""}
    `;
    tile.querySelector(".file-select input").addEventListener("change", event => {
      event.stopPropagation();
      if (event.target.checked) selectedFiles.add(file.relative_path);
      else selectedFiles.delete(file.relative_path);
      updateBulkBar();
      renderExplorer();
    });
    tile.addEventListener("dblclick", () => openFilePreview(file));
    tile.addEventListener("click", event => {
      if (!event.target.closest(".file-select")) openFilePreview(file);
    });
    grid.appendChild(tile);
  });
  updateBulkBar();
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
  request.setRequestHeader("X-CSRF-Token", document.querySelector('meta[name="csrf-token"]')?.content || "");
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
$("headerUploadBtn").addEventListener("click", () => {
  navigate("organize");
  setTimeout(() => $("fileInput").click(), 0);
});
$("sidebarCollapse").addEventListener("click", () => $("sidebar").classList.toggle("collapsed"));
$("mobileMenu").addEventListener("click", () => $("sidebar").classList.add("open"));
$("sidebarBackdrop").addEventListener("click", () => $("sidebar").classList.remove("open"));
$("themeToggle").addEventListener("click", () => applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light"));
$("themeSetting").addEventListener("change", event => applyTheme(event.target.checked ? "light" : "dark"));
$("themeSetting").checked = false;

["dragenter","dragover"].forEach(name => $("dropZone").addEventListener(name, event => { event.preventDefault(); $("dropZone").classList.add("drag-over"); }));
$("dropZone").addEventListener("dragleave", () => $("dropZone").classList.remove("drag-over"));
$("dropZone").addEventListener("drop", event => { event.preventDefault(); $("dropZone").classList.remove("drag-over"); addFiles(event.dataTransfer.files); });
$("dropZone").addEventListener("click", event => {
  if (!event.target.closest("label")) $("fileInput").click();
});
$("dropZone").addEventListener("keydown", event => { if (event.key==="Enter" || event.key===" ") $("fileInput").click(); });
$("fileInput").addEventListener("change", event => { addFiles(event.target.files); event.target.value = ""; });
$("folderInput").addEventListener("change", event => { addFiles(event.target.files); event.target.value = ""; });
$("clearQueueBtn").addEventListener("click", () => { queuedFiles=[]; updateQueue(); });
$("organizeBtn").addEventListener("click", previewOrganization);
$("cancelPreviewBtn").addEventListener("click", () => { previewReady=false; $("organizationPreview").hidden=true; });
$("applyOrganizationBtn").addEventListener("click", applyOrganization);

$("explorerSearch").addEventListener("input", renderExplorer);
$("explorerCategory").addEventListener("change", renderExplorer);
$("explorerExtension").addEventListener("change", renderExplorer);
$("explorerSize").addEventListener("change", renderExplorer);
$("explorerDate").addEventListener("change", renderExplorer);
$("explorerSort").addEventListener("change", renderExplorer);
$("gridViewBtn").addEventListener("click", () => { $("fileExplorer").classList.remove("list"); $("gridViewBtn").classList.add("active"); $("listViewBtn").classList.remove("active"); });
$("listViewBtn").addEventListener("click", () => { $("fileExplorer").classList.add("list"); $("listViewBtn").classList.add("active"); $("gridViewBtn").classList.remove("active"); });
$("selectAllFiles").addEventListener("change", event => {
  let visibleFiles = organizedFiles;
  const search = $("explorerSearch").value.trim().toLowerCase();
  const category = $("explorerCategory").value;
  const extension = $("explorerExtension").value;
  const sizeFilter = $("explorerSize").value;
  const dateFilter = $("explorerDate").value;
  visibleFiles = organizedFiles.filter(file => {
    const matchesSearch = !search || file.filename.toLowerCase().includes(search) || file.category.toLowerCase().includes(search) ||
      (file.tags || []).some(tag => tag.toLowerCase().includes(search)) || (file.collection || "").toLowerCase().includes(search);
    const age = Date.now() - new Date(file.modified).getTime();
    const matchesSize = sizeFilter === "all" || (sizeFilter === "small" && file.size < 100*1024) ||
      (sizeFilter === "medium" && file.size >= 100*1024 && file.size <= 5*1024*1024) ||
      (sizeFilter === "large" && file.size > 5*1024*1024);
    const matchesDate = dateFilter === "all" || (dateFilter === "today" && age < 86400000) ||
      (dateFilter === "week" && age < 604800000) || (dateFilter === "month" && age < 2592000000);
    return matchesSearch && (category === "all" || file.category === category) &&
      (extension === "all" || extensionOf(file.filename) === extension) && matchesSize && matchesDate;
  });
  visibleFiles.forEach(file => event.target.checked ? selectedFiles.add(file.relative_path) : selectedFiles.delete(file.relative_path));
  renderExplorer();
});
$("clearSelectionBtn").addEventListener("click", () => { selectedFiles.clear(); renderExplorer(); });
$("favoriteSelectedBtn").addEventListener("click", () => bulkUpdate("favorite"));
$("unfavoriteSelectedBtn").addEventListener("click", () => bulkUpdate("unfavorite"));
$("tagSelectedBtn").addEventListener("click", () => openConfirm(
  "Add tag",
  "A custom tag will be added to every selected file.",
  "tag",
  value => value ? bulkUpdate("add_tag", value) : showToast("Enter a tag name")
));
$("collectionSelectedBtn").addEventListener("click", () => openConfirm(
  "Add to collection",
  "Move the selected files into a named collection.",
  "collection",
  value => value ? bulkUpdate("collection", value) : showToast("Enter a collection name")
));
$("confirmCancelBtn").addEventListener("click", () => { confirmAction = null; $("confirmModal").hidden = true; });
$("confirmApplyBtn").addEventListener("click", () => {
  const input = $("confirmModalField").querySelector("input");
  const value = input ? input.value.trim() : null;
  const callback = confirmAction;
  confirmAction = null;
  $("confirmModal").hidden = true;
  if (callback) callback(value);
});
$("closePreviewModal").addEventListener("click", () => { $("filePreviewModal").hidden = true; lastPreviewFile = null; });
$("filePreviewModal").addEventListener("click", event => { if (event.target === $("filePreviewModal")) { $("filePreviewModal").hidden = true; lastPreviewFile = null; } });
$("previewFavoriteBtn").addEventListener("click", () => {
  if (!lastPreviewFile) return;
  selectedFiles = new Set([lastPreviewFile.relative_path]);
  bulkUpdate(lastPreviewFile.favorite ? "unfavorite" : "favorite").then(() => { $("filePreviewModal").hidden = true; });
});
window.addEventListener("keydown", event => {
  if (event.key === "Escape") {
    if (!$("filePreviewModal").hidden) $("closePreviewModal").click();
    if (!$("confirmModal").hidden) $("confirmCancelBtn").click();
  }
});

$("profileButton")?.addEventListener("click", () => window.location.assign("/profile"));
$("topbarProfileButton")?.addEventListener("click", () => window.location.assign("/profile"));
$("welcomeUploadBtn")?.addEventListener("click", () => {
  navigate("organize");
  setTimeout(() => $("fileInput").click(), 0);
});
$("sidebarLogoutBtn")?.addEventListener("click", async () => {
  try {
    const response = await fetch("/api/auth/logout", {
      method: "POST",
      headers: { "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]')?.content || "" }
    });
    const data = await response.json();
    window.location.assign(data.redirect || "/signin");
  } catch {
    showToast("Unable to log out. Please try again.");
  }
});

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
