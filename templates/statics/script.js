const MAX_UPLOAD_SIZE = 20 * 1024 * 1024;
const MAX_FILES_PER_UPLOAD = 100;
const FILE_CATEGORIES = ["Images", "Documents", "Videos", "Music", "Code", "Archives", "Others"];
const CATEGORY_LABELS = {
    Images: ["image", "images"],
    Documents: ["document", "documents"],
    Videos: ["video", "videos"],
    Music: ["music file", "music files"],
    Code: ["code file", "code files"],
    Archives: ["archive", "archives"],
    Others: ["other file", "other files"]
};
const fileInput = document.getElementById("fileInput");
const folderInput = document.getElementById("folderInput");
const dropZone = document.getElementById("dropZone");
const fileCount = document.getElementById("queueCount");
const queueSection = document.getElementById("queueSection");
const fileList = document.getElementById("fileList");
const queueMeta = document.getElementById("queueMeta");
const organizeBtn = document.getElementById("organizeBtn");
const statusMessage = document.getElementById("status");
const resultsPanel = document.getElementById("resultsPanel");
const results = document.getElementById("results");
const progressWrap = document.getElementById("progressWrap");
const progressFill = document.getElementById("progressFill");
const progressText = document.getElementById("progressText");
const progressPercent = document.getElementById("progressPercent");
const resultSearch = document.getElementById("resultSearch");
const categoryFilter = document.getElementById("categoryFilter");
const resultSort = document.getElementById("resultSort");
const emptySearch = document.getElementById("emptySearch");
const toast = document.getElementById("toast");
const clearQueueBtn = document.getElementById("clearQueueBtn");
const downloadZipBtn = document.getElementById("downloadZipBtn");
const downloadExpiry = document.getElementById("downloadExpiry");
const skipDuplicates = document.getElementById("skipDuplicates");

let queuedFiles = [];
let organizedFiles = [];
let organizationHistory = [];
let toastTimer;
let isUploading = false;
let duplicatesSkipped = false;

function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function getFileStyle(filename) {
    const extension = filename.split(".").pop().toLowerCase();
    if (["jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "tif", "tiff", "heic"].includes(extension)) return { icon: "▧", className: "image" };
    if (["mp3", "wav", "aac", "ogg", "flac", "m4a"].includes(extension)) return { icon: "♫", className: "audio" };
    if (["mp4", "mkv", "mov", "avi", "webm"].includes(extension)) return { icon: "▶", className: "video" };
    if (["zip", "rar", "7z", "tar", "gz"].includes(extension)) return { icon: "▦", className: "archive" };
    if (["py", "js", "ts", "html", "css", "json", "java", "c", "cpp", "cs", "go", "rs", "php", "rb", "sh", "sql", "xml", "yaml", "yml", "toml"].includes(extension)) return { icon: "</>", className: "code" };
    return { icon: "▤", className: "" };
}

function setStatus(message, kind = "") {
    statusMessage.textContent = message;
    statusMessage.className = `status-message${kind ? ` ${kind}` : ""}`;
}

function updateQueue() {
    const totalBytes = queuedFiles.reduce((sum, file) => sum + file.size, 0);
    fileCount.textContent = queuedFiles.length;
    queueMeta.textContent = `${queuedFiles.length} ${queuedFiles.length === 1 ? "file" : "files"} · ${formatBytes(totalBytes)} total`;
    queueSection.hidden = queuedFiles.length === 0;
    organizeBtn.disabled = queuedFiles.length === 0 || isUploading;
    clearQueueBtn.disabled = isUploading;
    fileList.replaceChildren();

    queuedFiles.forEach((file, index) => {
        const style = getFileStyle(file.name);
        const item = document.createElement("li");
        item.className = "file-item";

        const icon = document.createElement("span");
        icon.className = `file-type-icon ${style.className}`;
        icon.setAttribute("aria-hidden", "true");
        icon.textContent = style.icon;

        const details = document.createElement("div");
        details.className = "file-details";
        const name = document.createElement("p");
        name.className = "file-name";
        name.title = file.name;
        name.textContent = file.name;
        const meta = document.createElement("span");
        meta.className = "file-meta";
        meta.textContent = formatBytes(file.size);
        details.append(name, meta);

        const remove = document.createElement("button");
        remove.className = "remove-file";
        remove.type = "button";
        remove.setAttribute("aria-label", `Remove ${file.name}`);
        remove.innerHTML = '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m6 6 8 8m0-8-8 8" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>';
        remove.disabled = isUploading;
        remove.addEventListener("click", () => {
            queuedFiles.splice(index, 1);
            updateQueue();
            setStatus("");
        });

        item.append(icon, details, remove);
        fileList.appendChild(item);
    });
}

function addFiles(files) {
    if (isUploading) return;
    const rejectedForSize = [];
    const rejectedForCount = [];
    const accepted = [];
    let remainingSize = MAX_UPLOAD_SIZE - queuedFiles.reduce((sum, file) => sum + file.size, 0);

    Array.from(files).forEach(file => {
        if (queuedFiles.length + accepted.length >= MAX_FILES_PER_UPLOAD) {
            rejectedForCount.push(file.name);
            return;
        }
        if (file.size > remainingSize) {
            rejectedForSize.push(file.name);
        } else {
            accepted.push(file);
            remainingSize -= file.size;
        }
    });

    if (accepted.length) {
        queuedFiles.push(...accepted);
        updateQueue();
        setStatus("");
        progressWrap.hidden = true;
    }

    const errors = [];
    if (rejectedForSize.length) {
        const names = rejectedForSize.length > 1 ? `${rejectedForSize.length} files` : rejectedForSize[0];
        errors.push(`${names} would exceed the 20 MB total upload limit`);
    }
    if (rejectedForCount.length) {
        errors.push(`only ${MAX_FILES_PER_UPLOAD} files can be organized at once`);
    }
    if (errors.length) {
        setStatus(`${errors.join("; ")}.`, "error");
    }
}

function showToast(message) {
    toast.textContent = message;
    toast.classList.add("show");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toast.classList.remove("show"), 2600);
}

function renderResults() {
    const query = resultSearch.value.trim().toLowerCase();
    const selectedCategory = categoryFilter.value;
    const matchingFiles = organizedFiles.filter(file => {
        const matchesName = file.filename.toLowerCase().includes(query);
        const matchesCategory = selectedCategory === "all" || file.category === selectedCategory;
        return matchesName && matchesCategory;
    });
    const sortMode = resultSort.value;
    matchingFiles.sort((first, second) => {
        if (sortMode === "size-desc") return second.size - first.size || first.filename.localeCompare(second.filename);
        if (sortMode === "size-asc") return first.size - second.size || first.filename.localeCompare(second.filename);
        if (sortMode === "category") return first.category.localeCompare(second.category) || first.filename.localeCompare(second.filename);
        return first.filename.localeCompare(second.filename);
    });

    results.replaceChildren();
    matchingFiles.forEach((file, index) => {
        const style = getFileStyle(file.filename);
        const item = document.createElement("div");
        item.className = "result-item";
        item.style.animationDelay = `${Math.min(index * 25, 200)}ms`;

        const icon = document.createElement("span");
        icon.className = `file-type-icon ${style.className}`;
        icon.setAttribute("aria-hidden", "true");
        icon.textContent = style.icon;

        const info = document.createElement("div");
        info.className = "result-file-info";
        const name = document.createElement("p");
        name.className = "result-file-name";
        name.title = file.filename;
        name.textContent = file.filename;
        const category = document.createElement("span");
        category.className = "result-category";
        category.textContent = file.category;
        const meta = document.createElement("span");
        meta.className = "result-meta";
        meta.textContent = file.duplicate && duplicatesSkipped
            ? `${formatBytes(file.size)} · duplicate, not included`
            : `${formatBytes(file.size)}${file.duplicate ? " · duplicate copy" : ""}`;
        info.append(name, category, meta);

        const arrow = document.createElement("span");
        arrow.className = "result-arrow";
        arrow.setAttribute("aria-hidden", "true");
        arrow.textContent = "→";
        item.append(icon, info, arrow);
        results.appendChild(item);
    });

    emptySearch.hidden = matchingFiles.length > 0;
}

function renderOrganizationHistory() {
    const historyList = document.getElementById("historyList");
    historyList.replaceChildren();
    organizationHistory.forEach((run, index) => {
        const item = document.createElement("li");
        const details = document.createElement("details");
        details.className = "history-run";
        const summary = document.createElement("summary");
        const runInfo = document.createElement("div");
        runInfo.className = "history-details";
        const title = document.createElement("strong");
        title.textContent = `Run ${organizationHistory.length - index} · ${run.total} ${run.total === 1 ? "file" : "files"} organized`;
        const meta = document.createElement("span");
        meta.textContent = `${run.categoryCount} ${run.categoryCount === 1 ? "category" : "categories"} · ${run.size} · ${run.date}`;
        runInfo.append(title, meta);
        const badge = document.createElement("span");
        badge.className = "history-badge";
        badge.textContent = "ZIP ready";
        summary.append(runInfo, badge);
        const fileList = document.createElement("ul");
        fileList.className = "history-files";
        run.files.forEach(file => {
            const fileItem = document.createElement("li");
            const filename = document.createElement("span");
            filename.textContent = file.filename;
            const category = document.createElement("span");
            category.className = "history-file-category";
            category.textContent = file.category;
            fileItem.append(filename, category);
            fileList.appendChild(fileItem);
        });
        details.append(summary, fileList);
        item.appendChild(details);
        historyList.appendChild(item);
    });
}

function organizeFiles() {
    if (!queuedFiles.length) {
        setStatus("Add at least one file to get started.", "error");
        return;
    }

    const uploadSize = queuedFiles.reduce((sum, file) => sum + file.size, 0);
    const formData = new FormData();
    const removeDuplicates = skipDuplicates.checked;
    queuedFiles.forEach(file => formData.append("files", file));
    formData.append("skip_duplicates", String(removeDuplicates));

    isUploading = true;
    fileInput.disabled = true;
    folderInput.disabled = true;
    updateQueue();
    organizeBtn.querySelector("span").textContent = "Organizing…";
    setStatus("");
    progressWrap.hidden = false;
    progressFill.style.width = "0%";
    progressPercent.textContent = "0%";
    progressText.textContent = "Uploading your files…";

    const request = new XMLHttpRequest();
    request.open("POST", "/organize");
    request.upload.addEventListener("progress", event => {
        if (!event.lengthComputable) return;
        const percent = Math.min(100, Math.round((event.loaded / event.total) * 100));
        progressFill.style.width = `${percent}%`;
        progressPercent.textContent = `${percent}%`;
        progressText.textContent = percent === 100 ? "Sorting into folders…" : "Uploading your files…";
    });
    request.addEventListener("load", () => {
        let data;
        try {
            data = JSON.parse(request.responseText);
        } catch {
            setStatus("The server returned an unreadable response. Please try again.", "error");
            finishOrganization();
            return;
        }

        if (request.status < 200 || request.status >= 300) {
            setStatus(data.error || "Something went wrong while organizing your files.", "error");
            finishOrganization();
            return;
        }

        if (
            !Array.isArray(data.files)
            || typeof data.total !== "number"
            || typeof data.included !== "number"
            || typeof data.duplicates !== "number"
            || typeof data.download_url !== "string"
        ) {
            setStatus("The server response is missing file details. Please try again.", "error");
            finishOrganization();
            return;
        }

        organizedFiles = data.files;
        duplicatesSkipped = data.skip_duplicates === true;
        const includedFiles = organizedFiles.filter(file => !data.skip_duplicates || !file.duplicate);
        const categoryCounts = FILE_CATEGORIES
            .map(category => {
                const count = includedFiles.filter(file => file.category === category).length;
                return count ? `${count} ${CATEGORY_LABELS[category][count === 1 ? 0 : 1]}` : "";
            })
            .filter(Boolean);
        const breakdown = categoryCounts.length ? ` · ${categoryCounts.join(", ")}` : "";
        const categoryCount = new Set(includedFiles.map(file => file.category)).size;
        const duplicateMessage = data.duplicates
            ? data.skip_duplicates
                ? ` ${data.duplicates} exact ${data.duplicates === 1 ? "duplicate was" : "duplicates were"} skipped.`
                : ` ${data.duplicates} exact ${data.duplicates === 1 ? "duplicate was" : "duplicates were"} found and included.`
            : "";
        document.getElementById("resultSummary").textContent =
            `${data.included} ${data.included === 1 ? "file" : "files"} organized successfully${breakdown}.${duplicateMessage}`;
        document.getElementById("statFileCount").textContent = data.included.toLocaleString();
        document.getElementById("statCategoryCount").textContent = categoryCount.toLocaleString();
        document.getElementById("statUploadSize").textContent = formatBytes(uploadSize);
        document.getElementById("statDuplicateCount").textContent = data.duplicates.toLocaleString();
        document.getElementById("statDuplicateNote").textContent = data.skip_duplicates
            ? "exact matches skipped"
            : "exact matches included";
        organizationHistory.unshift({
            total: data.included,
            categoryCount,
            size: formatBytes(uploadSize),
            date: new Date().toLocaleString(),
            files: organizedFiles.map(file => ({ filename: file.filename, category: file.category }))
        });
        organizationHistory = organizationHistory.slice(0, 5);
        renderOrganizationHistory();
        downloadZipBtn.href = data.download_url;
        downloadZipBtn.hidden = false;
        downloadExpiry.hidden = false;
        categoryFilter.value = "all";
        resultSort.value = "name";
        resultSearch.value = "";
        renderResults();
        resultsPanel.hidden = false;
        setStatus("All done! Your files have found their homes.", "success");
        progressText.textContent = "Organization complete";
        progressFill.style.width = "100%";
        progressPercent.textContent = "100%";
        queuedFiles = [];
        updateQueue();
        fileInput.value = "";
        folderInput.value = "";
        resultsPanel.scrollIntoView({ behavior: "smooth", block: "start" });
        finishOrganization(true);
    });
    request.addEventListener("error", () => {
        setStatus("Could not reach the server. Check your connection and try again.", "error");
        finishOrganization();
    });
    request.addEventListener("timeout", () => {
        setStatus("The upload took too long. Please try again.", "error");
        finishOrganization();
    });
    request.timeout = 120000;
    request.send(formData);
}

function finishOrganization(success = false) {
    isUploading = false;
    fileInput.disabled = false;
    folderInput.disabled = false;
    organizeBtn.querySelector("span").textContent = "Organize files";
    updateQueue();
    if (!success) {
        window.setTimeout(() => {
            progressWrap.hidden = true;
            progressFill.style.width = "0%";
        }, 500);
    }
}

fileInput.addEventListener("change", event => {
    addFiles(event.target.files);
    fileInput.value = "";
});
folderInput.addEventListener("change", event => {
    addFiles(event.target.files);
    folderInput.value = "";
});

dropZone.addEventListener("click", event => {
    if (event.target.closest(".browse-link")) return;
    fileInput.click();
});
["dragenter", "dragover"].forEach(eventName => {
    dropZone.addEventListener(eventName, event => {
        event.preventDefault();
        dropZone.classList.add("drag-over");
    });
});
["dragleave", "drop"].forEach(eventName => {
    dropZone.addEventListener(eventName, event => {
        event.preventDefault();
        if (eventName === "drop") addFiles(event.dataTransfer.files);
        dropZone.classList.remove("drag-over");
    });
});

clearQueueBtn.addEventListener("click", () => {
    if (isUploading) return;
    queuedFiles = [];
    fileInput.value = "";
    updateQueue();
    setStatus("");
});
organizeBtn.addEventListener("click", organizeFiles);
resultSearch.addEventListener("input", renderResults);
categoryFilter.addEventListener("change", renderResults);
resultSort.addEventListener("change", renderResults);

document.getElementById("copySummaryBtn").addEventListener("click", async () => {
    const summary = organizedFiles.map(file =>
        `${file.filename} → ${file.category}${file.duplicate && duplicatesSkipped ? " (duplicate, not included)" : ""}`
    ).join("\n");
    try {
        await navigator.clipboard.writeText(summary);
        showToast("Organization summary copied.");
    } catch {
        showToast("Clipboard access is unavailable in this browser.");
    }
});

downloadZipBtn.addEventListener("click", () => {
    downloadZipBtn.hidden = true;
    downloadExpiry.hidden = true;
});

