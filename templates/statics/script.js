
const fileInput = document.getElementById("fileInput");
const fileCount = document.getElementById("fileCount");
const organizeBtn = document.getElementById("organizeBtn");
const status = document.getElementById("status");
const results = document.getElementById("results");

fileInput.addEventListener("change", function () {
    const total = fileInput.files.length;

    fileCount.textContent =
        total + (total === 1 ? " file selected" : " files selected");

    status.textContent = "";
});

organizeBtn.addEventListener("click", async function () {

    const files = fileInput.files;

    if (files.length === 0) {
        status.textContent = "Please select files first!";
        status.className = "error";
        return;
    }

    const formData = new FormData();

    for (let file of files) {
        formData.append("files", file);
    }

    organizeBtn.disabled = true;
    organizeBtn.textContent = "Organizing...";

    status.textContent = "Please wait...";
    status.className = "";

    try {
        const response = await fetch("/organize", {
            method: "POST",
            body: formData
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || "Something went wrong.");
        }

        status.textContent = data.message;
        status.className = "success";

        results.replaceChildren();

        const summary = document.createElement("p");
        summary.textContent = "Total files organized: " + data.total;
        results.appendChild(summary);

        data.files.forEach(file => {
            const item = document.createElement("p");
            item.textContent = "📁 " + file.filename + " → " + file.category;
            results.appendChild(item);
        });

        fileInput.value = "";
        fileCount.textContent = "No files selected";

    } catch (error) {
        status.textContent = error.message;
        status.className = "error";

    } finally {
        organizeBtn.disabled = false;
        organizeBtn.textContent = "Organize Files";
    }
});
