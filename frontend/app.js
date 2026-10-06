const form = document.getElementById("research-form");

const questionInput = document.getElementById("question");

const button = document.getElementById("research-button");

const roundsInput = document.getElementById("rounds");

const roundsNote = document.getElementById("rounds-tip");

const throttleToggle = document.getElementById("throttle-toggle");

const throttleNote = document.getElementById("throttle-tip");

const throttleStats = document.getElementById("throttle-stats");

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

const ROUND_LABELS = {
    1: "Quick",
    2: "Standard",
    3: "Deep",
    4: "Exhaustive",
};

let currentQuestion = "";

let currentMarkdown = "";

let progressiveHtml = "";


// The round ceiling is a server setting, so the options are fetched rather than
// hardcoded here. Without this the UI silently caps at whatever it shipped with.
async function loadRoundOptions() {
    if (!roundsInput) {
        return;
    }
    const fallback = [1, 2, 3, 4];
    let cfg = { rounds_ceiling: 4, rounds_default: 2, budget_estimate: {} };
    try {
        const response = await fetch("/api/config");
        if (response.ok) {
            cfg = await response.json();
        }
    } catch (error) {
        // Backend unreachable: fall back to the conservative set.
    }

    const ceiling = Math.max(1, Math.min(cfg.rounds_ceiling || 4, 10));
    roundsInput.textContent = "";
    for (let n = 1; n <= ceiling; n += 1) {
        const option = document.createElement("option");
        option.value = String(n);
        option.textContent =
            (ROUND_LABELS[n] || `Round ${n}`) + ` (${n} round${n > 1 ? "s" : ""})`;
        roundsInput.appendChild(option);
    }
    const preferred = Math.min(cfg.rounds_default || 2, ceiling);
    roundsInput.value = String(preferred);

    if (roundsNote) {
        const estimates = cfg.budget_estimate || {};
        const parts = [];
        for (let n = 2; n <= Math.min(ceiling, 4); n += 1) {
            if (estimates[String(n)]) {
                parts.push(`${n} rounds ≈ ${estimates[String(n)]} model calls`);
            }
        }
        if (parts.length) {
            roundsNote.textContent =
                "Deeper runs cost more model calls per round: " +
                parts.join(", ") +
                ". The budget scales automatically unless RESEARCH_LLM_BUDGET is set.";
            const info = roundsNote.closest(".info");
            if (info) {
                info.classList.add("has-tip");
            }
        }
    }
}

function renderThrottleStats(payload) {
    if (!throttleStats || !payload) {
        return;
    }
    const live = payload.llm || payload;
    throttleStats.textContent = "";

    const tiles = [
        [live.enabled ? "on" : "off", "pacing", "model calls"],
        [live.per_minute + "/min", "rate", "configured ceiling"],
        [live.calls || 0, "calls sent", "this process"],
        [Math.round(live.waited_seconds || 0) + "s", "deliberate waiting",
         "held back to stay under quota"],
    ];
    for (const [value, label, hint] of tiles) {
        throttleStats.appendChild(statTile(value, label, hint));
    }
}


function loadThrottleState() {
    if (!throttleToggle || !throttleNote) {
        return Promise.resolve();
    }
    return fetch("/api/throttle")
        .then((response) => (response.ok ? response.json() : null))
        .then((body) => {
            if (!body || !body.configured) {
                return;
            }
            const cfg = body.configured;
            throttleToggle.value = cfg.enabled ? "1" : "0";
            if (cfg.enabled) {
                const spacing = cfg.per_minute > 0
                    ? (60 / cfg.per_minute).toFixed(1)
                    : "0";
                throttleNote.textContent =
                    `Pacing model calls to ${cfg.per_minute}/min `
                    + `(~${spacing}s apart, ${cfg.max_concurrent} at a time). `
                    + "Slower, but far less likely to exhaust a free-tier quota. "
                    + "Choose Unthrottled once you are on paid plans with headroom.";
            } else {
                throttleNote.textContent =
                    "Pacing is off. Calls go out as fast as the pipeline allows, "
                    + "which risks exhausting free-tier quotas.";
            }
            const info = throttleNote.closest(".info");
            if (info) {
                info.classList.add("has-tip");
            }
        })
        .catch(() => { /* backend unreachable: keep the default selection */ });
}


loadRoundOptions();
loadThrottleState();


form.addEventListener("submit", async (event) => {
    event.preventDefault();

    const question = questionInput.value.trim();

    if (!question) {
        return;
    }

    currentQuestion = question;
    currentMarkdown = "";
    progressiveHtml = "";

    errorBox.classList.add("hidden");
    results.classList.remove("hidden");

    resetResults();
    report.classList.add("is-loading");

    startResearchButton();

    try {
        const rounds = roundsInput ? roundsInput.value : "2";
        const throttle = throttleToggle ? throttleToggle.value : "1";

        const response = await fetch(
            `/api/research?q=${encodeURIComponent(question)}`
            + `&rounds=${encodeURIComponent(rounds)}`
            + `&throttle=${encodeURIComponent(throttle)}`
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
        report.classList.remove("is-loading");
    } finally {
        stopResearchButton();
    }
});


const RESEARCH_MESSAGES = [
    "Collecting Ideas From The Internet",
    "Reading Source Documents",
    "Extracting Evidence",
    "Checking For Gaps",
    "Resolving Contradictions",
    "Writing Your Report",
];

let researchTimer = null;


function startResearchButton() {
    button.disabled = true;
    button.classList.add("is-researching");
    let index = 0;
    const show = () => {
        button.innerHTML = "";
        const msg = document.createElement("span");
        msg.className = "research-msg";
        msg.textContent = RESEARCH_MESSAGES[index % RESEARCH_MESSAGES.length];
        const dots = document.createElement("span");
        dots.className = "dots";
        dots.setAttribute("aria-hidden", "true");
        for (let i = 0; i < 3; i += 1) {
            const dot = document.createElement("i");
            dot.textContent = ".";
            dots.appendChild(dot);
        }
        button.appendChild(msg);
        button.appendChild(dots);
        index += 1;
    };
    show();
    researchTimer = setInterval(show, 2600);
}


function stopResearchButton() {
    if (researchTimer) {
        clearInterval(researchTimer);
        researchTimer = null;
    }
    button.classList.remove("is-researching");
    button.disabled = false;
    button.textContent = "Start Research →";
}


function resetResults() {
    planList.textContent = "";
    searchList.textContent = "";
    contradictionList.textContent = "";
    evidenceCount.textContent = "";
    report.textContent = "";
    reportWords.textContent = "";
    retrievalStats.textContent = "";
    retrievalTiles = {};
    for (const key in retrievalDisplayed) {
        retrievalDisplayed[key] = 0;
    }
    retrievalList.textContent = "";
    analysisStats.textContent = "";
    analysisNotes.textContent = "";
    throttleStats.textContent = "";

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

    if (data.throttle) {
        renderThrottleStats(data.throttle);
    }

    if (data.type === "report_section") {
        renderReportSection(data);
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


// Flowing retrieval counters: tiles persist across events and tick upward in
// small random steps toward each new cumulative total, so the dashboard reads
// as a pipeline in motion rather than jumping between fixed values.
const retrievalDisplayed = {
    retrieved: 0,
    attempted: 0,
    full: 0,
    partial: 0,
    metadata: 0,
    words: 0,
    chunks: 0,
    references: 0,
};

let retrievalTiles = {};


function flowTile(key, label, hint) {
    let entry = retrievalTiles[key];
    if (!entry) {
        const tile = document.createElement("div");
        tile.className = "stat-tile";
        const valueEl = document.createElement("strong");
        valueEl.textContent = "0";
        const labelEl = document.createElement("span");
        labelEl.textContent = label;
        tile.appendChild(valueEl);
        tile.appendChild(labelEl);
        if (hint) {
            const hintEl = document.createElement("small");
            hintEl.textContent = hint;
            tile.appendChild(hintEl);
        }
        retrievalStats.appendChild(tile);
        entry = { tile, valueEl, labelEl, timer: null };
        retrievalTiles[key] = entry;
    } else {
        entry.labelEl.textContent = label;
    }
    return entry;
}


function flowNumber(key, target, format) {
    const entry = retrievalTiles[key];
    if (!entry) {
        return;
    }
    target = Number(target) || 0;
    if (entry.timer) {
        clearInterval(entry.timer);
        entry.timer = null;
    }
    const render = (value) => {
        entry.valueEl.textContent = format ? format(value) : String(value);
    };
    const reduce = window.matchMedia
        && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce || target <= retrievalDisplayed[key]) {
        retrievalDisplayed[key] = target;
        render(target);
        return;
    }
    entry.timer = setInterval(() => {
        const remaining = target - retrievalDisplayed[key];
        if (remaining <= 0) {
            clearInterval(entry.timer);
            entry.timer = null;
            render(target);
            return;
        }
        const hop = Math.max(1, Math.round(remaining * (0.04 + Math.random() * 0.12)));
        retrievalDisplayed[key] += Math.min(hop, remaining);
        render(retrievalDisplayed[key]);
        if (retrievalDisplayed[key] >= target) {
            clearInterval(entry.timer);
            entry.timer = null;
        }
    }, 130);
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

    // Tiles persist across events: each new cumulative total flows upward
    // from the currently displayed value instead of restarting at zero.
    flowTile("retrieved", `of ${data.attempted || 0} attempted`, "documents fetched");
    flowNumber("retrieved", data.retrieved || 0);
    retrievalDisplayed.attempted = data.attempted || 0;
    flowTile("full", "full text", "readable end to end");
    flowNumber("full", data.full_text || 0);
    flowTile("partial", "partial", "paywall, truncation or PDF limits");
    flowNumber("partial", data.partial || 0);
    flowTile("metadata", "metadata only", "no document text");
    flowNumber("metadata", data.metadata_only || 0);
    flowTile("words", "words", "retrieved in total");
    flowNumber("words", data.words || 0, formatNumber);
    flowTile("chunks", "chunks", "section-aware passages");
    flowNumber("chunks", data.chunks || 0);

    if (data.references_followed) {
        flowTile("references", "references", "followed from sources");
        flowNumber("references", data.references_followed);
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
    } else if ((gap.missing || "").toLowerCase().includes("round limit")) {
        line.classList.add("is-complete");
        line.textContent = "Research complete — round limit reached.";
    } else {
        line.textContent = "Still researching.";
    }

    gapBox.appendChild(line);

    if (gap.missing
        && !(gap.missing || "").toLowerCase().includes("round limit")) {
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


function renderReportSection(data) {
    // Progressive section from the output pool: appended in document order so
    // the stream keeps flowing even while a fallback resumes a failed section
    // server-side. The final "report" event remains authoritative and replaces
    // this progressive rendering wholesale.
    let html = data.html || "";
    if (!html && data.data && window.renderMarkdown) {
        html = window.renderMarkdown(data.data);
    }
    if (!html && data.data) {
        html = "";
    }
    if (html) {
        progressiveHtml += (progressiveHtml ? "\n" : "") + html;
        report.classList.remove("is-loading");
        report.innerHTML = progressiveHtml;
        wireCitationTooltips();
    }
}


function renderReport(data) {
    currentMarkdown = data.data || "";
    progressiveHtml = "";
    report.classList.remove("is-loading");

    // Server pre-compiles markdown to HTML to avoid freezing the UI on
    // 15k-word reports. The client renderer is only a fallback for old payloads.
    if (data.html) {
        report.innerHTML = data.html;
    } else if (window.renderMarkdown) {
        report.innerHTML = window.renderMarkdown(currentMarkdown);
    } else {
        report.textContent = currentMarkdown;
    }
    wireCitationTooltips();

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


function wireCitationTooltips() {
    const cites = report.querySelectorAll("a.citation");
    for (const cite of cites) {
        if (cite.dataset.wired) {
            continue;
        }
        cite.dataset.wired = "1";
        cite.addEventListener("mouseenter", () => {
            const number = cite.dataset.ref || cite.textContent.replace(/[^0-9]/g, "");
            const target = number && document.getElementById(`ref-${number}`);
            if (!target) {
                return;
            }
            const popup = document.createElement("span");
            popup.className = "cite-popup";
            const title = document.createElement("strong");
            title.textContent = `Reference [${number}]`;
            popup.appendChild(title);
            const body = document.createElement("span");
            body.textContent = target.textContent.trim().slice(0, 400);
            popup.appendChild(body);
            cite.appendChild(popup);
            const rect = cite.getBoundingClientRect();
            popup.style.left = "0";
            popup.style.bottom = "1.4em";
            if (rect.left < 340) {
                popup.style.left = "0";
            }
        });
        cite.addEventListener("mouseleave", () => {
            const popup = cite.querySelector(".cite-popup");
            if (popup) {
                popup.remove();
            }
        });
        cite.addEventListener("focus", () => {
            cite.dispatchEvent(new Event("mouseenter"));
        });
        cite.addEventListener("blur", () => {
            cite.dispatchEvent(new Event("mouseleave"));
        });
    }
}


(function initSettingsMenu() {
    const button = document.getElementById("settings-button");
    const menu = document.getElementById("settings-menu");
    if (!button || !menu) {
        return;
    }
    function setOpen(open) {
        menu.classList.toggle("hidden", !open);
        button.setAttribute("aria-expanded", open ? "true" : "false");
    }
    button.addEventListener("click", (event) => {
        event.stopPropagation();
        setOpen(menu.classList.contains("hidden"));
    });
    menu.addEventListener("click", (event) => {
        event.stopPropagation();
    });
    document.addEventListener("click", () => setOpen(false));
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            setOpen(false);
        }
    });
})();


(function initTheme() {
    const root = document.documentElement;
    const toggle = document.getElementById("theme-toggle");
    const label = toggle ? toggle.querySelector(".theme-label") : null;
    let saved = null;
    try {
        saved = localStorage.getItem("dr-theme");
    } catch (error) {
        saved = null;
    }
    if (saved === "light") {
        root.dataset.theme = "light";
    }
    function syncLabel() {
        const light = root.dataset.theme === "light";
        if (label) {
            label.textContent = light ? "Dark mode" : "Light mode";
        } else if (toggle) {
            toggle.textContent = light ? "Dark mode" : "Light mode";
        }
        if (toggle) {
            toggle.title = light ? "Switch to dark mode" : "Switch to light mode";
        }
    }
    syncLabel();
    if (toggle) {
        toggle.addEventListener("click", () => {
            const apply = () => {
                const light = root.dataset.theme !== "light";
                if (light) {
                    root.dataset.theme = "light";
                } else {
                    delete root.dataset.theme;
                }
                try {
                    localStorage.setItem("dr-theme", light ? "light" : "dark");
                } catch (error) {
                    /* private mode: theme just does not persist */
                }
                syncLabel();
                toggle.classList.remove("theme-spin");
                void toggle.offsetWidth;
                toggle.classList.add("theme-spin");
            };
            // Circular reveal from the top-left corner to the bottom-right
            // in 0.3s. Falls back to an instant swap where unsupported.
            if (document.startViewTransition) {
                document.startViewTransition(apply);
            } else {
                apply();
            }
        });
    }
})();