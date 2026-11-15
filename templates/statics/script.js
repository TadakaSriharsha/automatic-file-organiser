const MAX_UPLOAD_SIZE = 20 * 1024 * 1024;
const FILE_CATEGORIES = ["Images", "Documents", "Videos", "Audio", "Archives", "Others"];
const CATEGORY_LABELS = {
    Images: ["image", "images"],
    Documents: ["document", "documents"],
    Videos: ["video", "videos"],
    Audio: ["audio file", "audio files"],
    Archives: ["archive", "archives"],
    Others: ["other file", "other files"]
};
const fileInput = document.getElementById("fileInput");
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
const emptySearch = document.getElementById("emptySearch");
const toast = document.getElementById("toast");
const clearQueueBtn = document.getElementById("clearQueueBtn");
const aiFileInput = document.getElementById("aiFileInput");
const aiFileName = document.getElementById("aiFileName");
const aiInstruction = document.getElementById("aiInstruction");
const aiPromptCount = document.getElementById("aiPromptCount");
const aiConsent = document.getElementById("aiConsent");
const aiEditBtn = document.getElementById("aiEditBtn");
const aiStatus = document.getElementById("aiStatus");
const aiProgress = document.getElementById("aiProgress");
const aiProgressText = document.getElementById("aiProgressText");
const aiProgressPercent = document.getElementById("aiProgressPercent");
const aiProgressFill = document.getElementById("aiProgressFill");
const aiDownload = document.getElementById("aiDownload");
const aiDownloadLink = document.getElementById("aiDownloadLink");

let queuedFiles = [];
let organizedFiles = [];
let toastTimer;
let isUploading = false;
let selectedAiFile = null;
let isAiEditing = false;
let aiOutputUrl = null;

function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function getFileStyle(filename) {
    const extension = filename.split(".").pop().toLowerCase();
    if (["jpg", "jpeg", "png", "gif", "webp"].includes(extension)) return { icon: "▧", className: "image" };
    if (["mp3", "wav", "aac", "ogg"].includes(extension)) return { icon: "♫", className: "audio" };
    if (["mp4", "mkv", "mov", "avi"].includes(extension)) return { icon: "▶", className: "video" };
    if (["zip", "rar", "7z"].includes(extension)) return { icon: "▦", className: "archive" };
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
    const rejected = [];
    const accepted = [];
    let remainingSize = MAX_UPLOAD_SIZE - queuedFiles.reduce((sum, file) => sum + file.size, 0);

    Array.from(files).forEach(file => {
        if (file.size > remainingSize) {
            rejected.push(file.name);
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

    if (rejected.length) {
        const names = rejected.length > 1 ? `${rejected.length} files` : rejected[0];
        setStatus(`${names} would exceed the 20 MB total upload limit and were not added.`, "error");
    }
}

function showToast(message) {
    toast.textContent = message;
    toast.classList.add("show");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toast.classList.remove("show"), 2600);
}

function setAIStatus(message, kind = "") {
    aiStatus.textContent = message;
    aiStatus.className = `ai-status${kind ? ` ${kind}` : ""}`;
}

function clearAIOutput() {
    if (aiOutputUrl) {
        URL.revokeObjectURL(aiOutputUrl);
        aiOutputUrl = null;
    }
    aiDownload.hidden = true;
    aiDownloadLink.removeAttribute("href");
    aiProgress.hidden = true;
    aiProgressFill.style.width = "0%";
    aiProgressFill.classList.remove("is-working");
}

function updateAIButton() {
    aiEditBtn.disabled = isAiEditing || !selectedAiFile || !aiInstruction.value.trim() || !aiConsent.checked;
    aiFileInput.disabled = isAiEditing;
    aiInstruction.disabled = isAiEditing;
    aiConsent.disabled = isAiEditing;
}

function selectAIFile(file) {
    selectedAiFile = null;
    clearAIOutput();
    if (!file) {
        aiFileName.textContent = "No document selected";
        setAIStatus("");
        updateAIButton();
        return;
    }

    if (!file.name.toLowerCase().endsWith(".docx")) {
        aiFileInput.value = "";
        aiFileName.textContent = "No document selected";
        setAIStatus("Choose a Word .docx file.", "error");
        updateAIButton();
        return;
    }

    if (file.size > MAX_UPLOAD_SIZE) {
        aiFileInput.value = "";
        aiFileName.textContent = "No document selected";
        setAIStatus("The document exceeds the 20 MB limit.", "error");
        updateAIButton();
        return;
    }

    selectedAiFile = file;
    aiFileName.textContent = `${file.name} · ${formatBytes(file.size)}`;
    aiFileName.title = file.name;
    setAIStatus("");
    updateAIButton();
}

function editDocumentWithAI() {
    if (!selectedAiFile || !aiInstruction.value.trim() || !aiConsent.checked || isAiEditing) return;

    clearAIOutput();
    setAIStatus("");
    aiProgress.hidden = false;
    aiProgressText.textContent = "Sending your document…";
    aiProgressPercent.textContent = "0%";
    aiProgressFill.style.width = "0%";
    aiProgressFill.classList.remove("is-working");
    isAiEditing = true;
    aiEditBtn.querySelector("span").textContent = "Working…";
    updateAIButton();

    const formData = new FormData();
    formData.append("document", selectedAiFile);
    formData.append("instruction", aiInstruction.value.trim());
    formData.append("consent", "true");

    const request = new XMLHttpRequest();
    request.open("POST", "/ai-edit");
    request.responseType = "blob";
    request.upload.addEventListener("progress", event => {
        if (!event.lengthComputable) return;
        const percent = Math.min(100, Math.round((event.loaded / event.total) * 100));
        aiProgressFill.style.width = `${percent}%`;
        aiProgressPercent.textContent = `${percent}%`;
        if (percent === 100) {
            aiProgressText.textContent = "Gemini is editing your document…";
            aiProgressPercent.textContent = "Working";
            aiProgressFill.classList.add("is-working");
        }
    });
    request.addEventListener("load", async () => {
        if (request.status < 200 || request.status >= 300) {
            let message = "AI editing failed. Please try again.";
            try {
                const data = JSON.parse(await request.response.text());
                if (typeof data.error === "string") message = data.error;
            } catch {
                message = "The server returned an unreadable error. Please try again.";
            }
            setAIStatus(message, "error");
            finishAIEdit();
            return;
        }

        const contentType = request.getResponseHeader("Content-Type") || "";
        if (!contentType.includes("wordprocessingml.document") || !request.response.size) {
            setAIStatus("The server did not return an edited Word document.", "error");
            finishAIEdit();
            return;
        }

        aiOutputUrl = URL.createObjectURL(request.response);
        const outputStem = selectedAiFile.name.replace(/\.docx$/i, "") || "document";
        aiDownloadLink.href = aiOutputUrl;
        aiDownloadLink.download = `${outputStem}_edited.docx`;
        aiDownload.hidden = false;
        setAIStatus("Your edited copy is ready to download.", "success");
        aiProgressText.textContent = "Edit complete";
        aiProgressPercent.textContent = "100%";
        aiProgressFill.classList.remove("is-working");
        aiProgressFill.style.width = "100%";
        finishAIEdit(true);
    });
    request.addEventListener("error", () => {
        setAIStatus("Could not reach the server. Check your connection and try again.", "error");
        finishAIEdit();
    });
    request.addEventListener("timeout", () => {
        setAIStatus("AI editing took too long. Please try again.", "error");
        finishAIEdit();
    });
    request.timeout = 180000;
    request.send(formData);
}

function finishAIEdit(success = false) {
    isAiEditing = false;
    aiEditBtn.querySelector("span").textContent = "Edit with AI";
    updateAIButton();
    if (!success) {
        aiProgressFill.classList.remove("is-working");
        window.setTimeout(() => {
            aiProgress.hidden = true;
            aiProgressFill.style.width = "0%";
        }, 500);
    }
}

function renderResults() {
    const query = resultSearch.value.trim().toLowerCase();
    const selectedCategory = categoryFilter.value;
    const matchingFiles = organizedFiles.filter(file => {
        const matchesName = file.filename.toLowerCase().includes(query);
        const matchesCategory = selectedCategory === "all" || file.category === selectedCategory;
        return matchesName && matchesCategory;
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
        info.append(name, category);

        const arrow = document.createElement("span");
        arrow.className = "result-arrow";
        arrow.setAttribute("aria-hidden", "true");
        arrow.textContent = "→";
        item.append(icon, info, arrow);
        results.appendChild(item);
    });

    emptySearch.hidden = matchingFiles.length > 0;
}

function organizeFiles() {
    if (!queuedFiles.length) {
        setStatus("Add at least one file to get started.", "error");
        return;
    }

    const formData = new FormData();
    queuedFiles.forEach(file => formData.append("files", file));

    isUploading = true;
    fileInput.disabled = true;
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

        if (!Array.isArray(data.files) || typeof data.total !== "number") {
            setStatus("The server response is missing file details. Please try again.", "error");
            finishOrganization();
            return;
        }

        organizedFiles = data.files;
        const categoryCounts = FILE_CATEGORIES
            .map(category => {
                const count = organizedFiles.filter(file => file.category === category).length;
                return count ? `${count} ${CATEGORY_LABELS[category][count === 1 ? 0 : 1]}` : "";
            })
            .filter(Boolean);
        const breakdown = categoryCounts.length ? ` · ${categoryCounts.join(", ")}` : "";
        document.getElementById("resultSummary").textContent =
            `${data.total} ${data.total === 1 ? "file" : "files"} organized successfully${breakdown}.`;
        categoryFilter.value = "all";
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

dropZone.addEventListener("click", event => {
    if (event.target.closest(".browse-link")) return;
    fileInput.click();
});
dropZone.addEventListener("keydown", event => {
    if (event.target === dropZone && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault();
        fileInput.click();
    }
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

document.getElementById("copySummaryBtn").addEventListener("click", async () => {
    const summary = organizedFiles.map(file => `${file.filename} → ${file.category}`).join("\n");
    try {
        await navigator.clipboard.writeText(summary);
        showToast("Organization summary copied.");
    } catch {
        showToast("Clipboard access is unavailable in this browser.");
    }
});

aiFileInput.addEventListener("change", event => selectAIFile(event.target.files[0]));
aiInstruction.addEventListener("input", () => {
    aiPromptCount.textContent = `${aiInstruction.value.length.toLocaleString()} / 4,000`;
    clearAIOutput();
    setAIStatus("");
    updateAIButton();
});
aiConsent.addEventListener("change", updateAIButton);
aiEditBtn.addEventListener("click", editDocumentWithAI);
