const form = document.getElementById("research-form");

const questionInput = document.getElementById("question");

const button = document.getElementById("research-button");

const roundsInput = document.getElementById("rounds");

const errorBox = document.getElementById("error");

const results = document.getElementById("results");

const resultQuestion = document.getElementById("result-question");

const evidenceCount = document.getElementById("evidence-count");

const planList = document.getElementById("plan");

const searchList = document.getElementById("searches");

const gapCard = document.getElementById("gap-card");

const gapBox = document.getElementById("gap");

const contradictionCard = document.getElementById("contradiction-card");

const contradictionList = document.getElementById("contradictions");

const report = document.getElementById("report");

const retrievalStats = document.getElementById("retrieval-stats");

const retrievalNote = document.getElementById("retrieval-note");

const retrievalDetails = document.getElementById("retrieval-details");

const retrievalList = document.getElementById("retrieval-list");

const analysisStats = document.getElementById("analysis-stats");

const analysisNotes = document.getElementById("analysis-notes");

const downloadButton = document.getElementById("download-button");

const copyButton = document.getElementById("copy-button");

const reportWords = document.getElementById("report-words");

const statusBanner = document.getElementById("status-banner");

const statusMissing = document.getElementById("status-missing");

const reportLabel = document.getElementById("report-label");

const reportCard = document.querySelector(".report-card");

let currentQuestion = "";

let currentMarkdown = "";


form.addEventListener("submit", async (event) => {
    event.preventDefault();

    const question = questionInput.value.trim();

    if (!question) {
        return;
    }

    currentQuestion = question;
    currentMarkdown = "";

    errorBox.classList.add("hidden");
    results.classList.remove("hidden");

    resetResults();

    button.disabled = true;
    button.textContent = "Researching...";

    try {
        const rounds = roundsInput ? roundsInput.value : "2";

        const response = await fetch(
            `/api/research?q=${encodeURIComponent(question)}&rounds=${encodeURIComponent(rounds)}`
        );

        if (!response.ok) {
            throw new Error("Research request failed.");
        }

        if (!response.body) {
            throw new Error("Research stream is unavailable.");
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();

        let buffer = "";
        let finished = false;

        while (!finished) {
            const { value, done } = await reader.read();

            if (done) {
                break;
            }

            buffer += decoder.decode(value, { stream: true });

            const events = buffer.split("\n\n");
            buffer = events.pop();

            for (const eventText of events) {
                const line = eventText
                    .split("\n")
                    .find((line) => line.startsWith("data: "));

                if (!line) {
                    continue;
                }

                const data = JSON.parse(line.slice(6));

                if (data.type === "error") {
                    throw new Error(data.message || "Research failed.");
                }

                if (data.type === "done") {
                    finished = true;

                    break;
                }

                displayEvent(data, question);
            }
        }
    } catch (error) {
        errorBox.textContent = error.message;
        errorBox.classList.remove("hidden");
    } finally {
        button.disabled = false;
        button.textContent = "Start Research →";
    }
});


function resetResults() {
    planList.textContent = "";
    searchList.textContent = "";
    contradictionList.textContent = "";
    evidenceCount.textContent = "";
    report.textContent = "";
    reportWords.textContent = "";
    retrievalStats.textContent = "";
    retrievalList.textContent = "";
    analysisStats.textContent = "";
    analysisNotes.textContent = "";

    retrievalDetails.classList.add("hidden");

    gapBox.textContent = "";
    gapCard.classList.add("hidden");
    contradictionCard.classList.add("hidden");

    downloadButton.classList.add("hidden");
    copyButton.classList.add("hidden");

    statusBanner.textContent = "";
    statusBanner.className = "status-banner hidden";
    statusMissing.textContent = "";
    statusMissing.classList.add("hidden");

    if (reportCard) {
        reportCard.classList.remove("is-partial", "is-failed");
    }
    if (reportLabel) {
        reportLabel.textContent = "FINAL REPORT";
    }
}


function displayEvent(data, question) {
    resultQuestion.textContent = question;

    if (data.type === "plan") {
        renderPlan(data.data);
    }

    if (data.type === "results") {
        renderSearch(data);
    }

    if (data.type === "evidence") {
        evidenceCount.textContent = `${data.count} sources collected`;
    }

    if (data.type === "retrieval") {
        renderRetrieval(data.data);
    }

    if (data.type === "analysis") {
        renderAnalysis(data.data);
    }

    if (data.type === "gap") {
        renderGap(data.data);
    }

    if (data.type === "contradictions") {
        renderContradictions(data.data);
    }

    if (data.type === "status") {
        renderStatus(data.data);
    }

    if (data.type === "report") {
        if (data.status) {
            renderStatus(data.status);
        }
        renderReport(data);
    }

    results.scrollIntoView({
        behavior: "smooth",
        block: "start"
    });
}


function statTile(label, value, hint) {
    const tile = document.createElement("div");

    tile.className = "stat-tile";

    const valueEl = document.createElement("strong");

    valueEl.textContent = String(value);

    const labelEl = document.createElement("span");

    labelEl.textContent = label;

    tile.appendChild(valueEl);
    tile.appendChild(labelEl);

    if (hint) {
        const hintEl = document.createElement("small");

        hintEl.textContent = hint;
        tile.appendChild(hintEl);
    }

    return tile;
}


function renderRetrieval(data) {
    if (!data || data.enabled === false) {
        retrievalNote.textContent =
            (data && data.note) ||
            "Full-text retrieval is disabled; only search snippets were used. "
            + "No document was read, so no finding below rests on document text.";

        return;
    }

    retrievalNote.textContent =
        "Documents were downloaded and parsed, not summarised from snippets. "
        + "Figures are cumulative across the whole research run, so they cover "
        + "every round, not just the most recent one.";

    retrievalStats.textContent = "";

    retrievalStats.appendChild(
        statTile(data.retrieved || 0, `${data.attempted || 0} attempted`,
            "documents fetched")
    );
    retrievalStats.appendChild(
        statTile(data.full_text || 0, "full text", "readable end to end")
    );
    retrievalStats.appendChild(
        statTile(data.partial || 0, "partial", "paywall, truncation or PDF limits")
    );
    retrievalStats.appendChild(
        statTile(data.metadata_only || 0, "metadata only", "no document text")
    );
    retrievalStats.appendChild(
        statTile(formatNumber(data.words || 0), "words", "retrieved in total")
    );
    retrievalStats.appendChild(
        statTile(data.chunks || 0, "chunks", "section-aware passages")
    );

    if (data.references_followed) {
        retrievalStats.appendChild(
            statTile(data.references_followed, "references", "followed from sources")
        );
    }

    const sources = data.sources || [];

    if (sources.length) {
        retrievalList.textContent = "";

        for (const source of sources) {
            const row = document.createElement("div");

            row.className = `retrieval-row status-${source.status}`;

            const badge = document.createElement("span");

            badge.className = "status-badge";
            badge.textContent = source.status.replace("_", " ");

            const title = document.createElement("a");

            title.href = source.url || "#";
            title.target = "_blank";
            title.rel = "noopener noreferrer";
            title.textContent = source.title || "(untitled)";

            const meta = document.createElement("small");

            meta.textContent = [
                source.method,
                `${formatNumber(source.words || 0)} words`,
                source.limitation
            ].filter(Boolean).join(" · ");

            row.appendChild(badge);
            row.appendChild(title);
            row.appendChild(meta);

            retrievalList.appendChild(row);
        }

        retrievalDetails.classList.remove("hidden");
    }
}


function renderAnalysis(data) {
    analysisStats.textContent = "";

    if (!data) {
        return;
    }

    analysisStats.appendChild(
        statTile(data.records || 0, "findings", "extracted and verified")
    );

    const dimensions = data.dimensions || [];

    analysisStats.appendChild(
        statTile(dimensions.length, "dimensions", "covered by evidence")
    );

    if (data.sources_summarised) {
        analysisStats.appendChild(
            statTile(data.sources_summarised, "sources", "individually assessed")
        );
    }

    const notes = data.notes || [];

    analysisNotes.textContent = "";

    for (const note of notes) {
        const item = document.createElement("li");

        item.textContent = note;
        analysisNotes.appendChild(item);
    }
}


function renderPlan(plan) {
    planList.textContent = "";

    const subquestions = (plan && plan.subquestions) || [];

    if (!subquestions.length) {
        const li = document.createElement("li");

        li.textContent = "No sub-questions were planned.";
        planList.appendChild(li);

        return;
    }

    for (const subquestion of subquestions) {
        const li = document.createElement("li");

        li.textContent = subquestion.question || "";

        const searches = subquestion.searches || [];

        if (searches.length) {
            const tags = document.createElement("div");

            tags.className = "plan-sources";

            for (const search of searches) {
                const tag = document.createElement("span");

                tag.className = "plan-source";
                tag.textContent = search.source || "web";

                tags.appendChild(tag);
            }

            li.appendChild(tags);
        }

        planList.appendChild(li);
    }
}


function renderSearch(data) {
    const item = document.createElement("article");

    item.className = "finding";

    if (data.error) {
        item.classList.add("finding-error");
    }

    const number = document.createElement("span");

    number.className = "finding-number";
    number.textContent = String(
        searchList.children.length + 1
    ).padStart(2, "0");

    const body = document.createElement("div");

    const heading = document.createElement("h5");

    heading.textContent = data.query || "(no query)";

    const detail = document.createElement("p");

    detail.textContent = data.error
        ? `${data.source || "search"} failed: ${data.error}`
        : `${data.source || "search"} returned ${data.count} result${
              data.count === 1 ? "" : "s"
          }`;

    body.appendChild(heading);
    body.appendChild(detail);

    item.appendChild(number);
    item.appendChild(body);

    searchList.appendChild(item);
}


function renderGap(gap) {
    if (!gap) {
        return;
    }

    gapBox.textContent = "";

    const line = document.createElement("p");

    line.className = "gap-line";

    if (gap.sufficient) {
        line.classList.add("is-sufficient");
        line.textContent = "Evidence looks sufficient.";
    } else {
        line.textContent = "Still researching.";
    }

    gapBox.appendChild(line);

    if (gap.missing) {
        const missing = document.createElement("p");

        missing.className = "gap-missing";
        missing.textContent = `Missing: ${gap.missing}`;

        gapBox.appendChild(missing);
    }

    const followUps = gap.follow_ups || [];

    if (followUps.length) {
        const list = document.createElement("ul");

        list.className = "follow-ups";

        for (const followUp of followUps) {
            const li = document.createElement("li");

            li.textContent = `${followUp.source} — ${followUp.query}`;

            list.appendChild(li);
        }

        gapBox.appendChild(list);
    }

    gapCard.classList.remove("hidden");
}


function renderContradictions(contradictions) {
    if (!Array.isArray(contradictions) || !contradictions.length) {
        return;
    }

    contradictionList.textContent = "";

    for (const item of contradictions) {
        const row = document.createElement("article");

        row.className = "finding";

        const body = document.createElement("div");

        const heading = document.createElement("h5");

        heading.textContent = item.topic || "Unclear topic";

        body.appendChild(heading);

        if (item.side_a) {
            const sideA = document.createElement("p");

            sideA.innerHTML =
                "<strong>A:</strong> " + window.escapeHtml(item.side_a);
            body.appendChild(sideA);
        }

        if (item.side_b) {
            const sideB = document.createElement("p");

            sideB.innerHTML =
                "<strong>B:</strong> " + window.escapeHtml(item.side_b);
            body.appendChild(sideB);
        }

        if (item.likely_cause) {
            const cause = document.createElement("p");

            cause.className = "gap-missing";
            cause.textContent = `Likely cause: ${item.likely_cause}`;
            body.appendChild(cause);
        }

        row.appendChild(body);

        contradictionList.appendChild(row);
    }

    contradictionCard.classList.remove("hidden");
}


const STATUS_TEXT = {
    completed: "COMPLETE REPORT",
    partial: "PARTIAL REPORT",
    failed: "REPORT FAILED"
};

const STATUS_HINT = {
    completed: "All planned sections were generated.",
    partial: "This research run did not finish. Treat the report as incomplete.",
    failed: "No usable report could be generated."
};


function renderStatus(status) {
    if (!status || !status.status) {
        return;
    }

    const state = status.status;

    statusBanner.className = "status-banner state-" + state;
    statusBanner.textContent = status.headline || STATUS_TEXT[state] || state;

    const hint = document.createElement("small");

    hint.textContent = STATUS_HINT[state] || "";
    statusBanner.appendChild(hint);

    const missing = status.missing_sections || [];

    if (missing.length) {
        statusMissing.textContent = "";
        for (const item of missing) {
            const li = document.createElement("li");
            li.textContent = item;
            statusMissing.appendChild(li);
        }
        statusMissing.classList.remove("hidden");
    } else {
        statusMissing.textContent = "";
        statusMissing.classList.add("hidden");
    }

    statusBanner.classList.remove("hidden");

    // A partial or failed run must not look like an ordinary finished report.
    if (reportCard) {
        reportCard.classList.toggle("is-partial", state === "partial");
        reportCard.classList.toggle("is-failed", state === "failed");
    }
    if (reportLabel && state !== "completed") {
        reportLabel.textContent = (STATUS_TEXT[state] || state) + " — INCOMPLETE";
    }
}


function renderReport(data) {
    currentMarkdown = data.data || "";

    // Older runs may return plain text rather than markdown.
    if (window.renderMarkdown) {
        report.innerHTML = window.renderMarkdown(currentMarkdown);
    } else {
        report.textContent = currentMarkdown;
    }

    const words = currentMarkdown.split(/\s+/).filter(Boolean).length;

    reportWords.textContent = `${formatNumber(words)} words`;

    const citations = report.querySelectorAll("a.citation").length;

    if (citations) {
        reportWords.textContent += ` · ${citations} citations`;
    }

    const sources = data.sources || [];

    if (sources.length) {
        const urls = sources
            .map((source) => source.url)
            .filter(Boolean);

        const unique = new Set(urls);

        evidenceCount.textContent =
            `${unique.size} sources · ${formatNumber(words)} words`;
    }

    const exportMeta = data.export || {};

    if (exportMeta.written) {
        downloadButton.classList.remove("hidden");
    }

    copyButton.classList.remove("hidden");

    wireDownload();
}


function wireDownload() {
    downloadButton.onclick = () => {
        // The browser cannot write to disk, so the server's export file is
        // served back as a download rather than faked client-side.
        window.location.href =
            `/api/report/download?q=${encodeURIComponent(currentQuestion)}`;
    };

    copyButton.onclick = async () => {
        try {
            await navigator.clipboard.writeText(currentMarkdown);
            copyButton.textContent = "Copied";

            setTimeout(() => {
                copyButton.textContent = "Copy markdown";
            }, 1500);
        } catch (error) {
            copyButton.textContent = "Copy failed";
        }
    };
}


function formatNumber(value) {
    return Number(value || 0).toLocaleString("en-US");
}