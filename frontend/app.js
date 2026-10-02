const form = document.getElementById("research-form");

const questionInput = document.getElementById("question");

const button = document.getElementById("research-button");

const errorBox = document.getElementById("error");

const results = document.getElementById("results");

const resultQuestion =
    document.getElementById("result-question");

const evidenceCount =
    document.getElementById("evidence-count");

const planList =
    document.getElementById("plan");

const searchList =
    document.getElementById("searches");

const gapCard =
    document.getElementById("gap-card");

const gapBox =
    document.getElementById("gap");

const contradictionCard =
    document.getElementById("contradiction-card");

const contradictionList =
    document.getElementById("contradictions");

const report =
    document.getElementById("report");


form.addEventListener("submit", async (event) => {
    event.preventDefault();

    const question = questionInput.value.trim();

    if (!question) {
        return;
    }

    errorBox.classList.add("hidden");
    results.classList.add("hidden");

    resetResults();

    button.disabled = true;
    button.textContent = "Researching...";

    try {
        const response = await fetch(
            `/api/research?q=${encodeURIComponent(question)}`
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

    gapBox.textContent = "";
    gapCard.classList.add("hidden");
    contradictionCard.classList.add("hidden");
}


function displayEvent(data, question) {
    results.classList.remove("hidden");

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

    if (data.type === "gap") {
        renderGap(data.data);
    }

    if (data.type === "contradictions") {
        renderContradictions(data.data);
    }

    if (data.type === "report") {
        report.textContent = data.data || "";
    }

    results.scrollIntoView({
        behavior: "smooth"
    });
}


function renderPlan(plan) {
    planList.textContent = "";

    const subquestions =
        (plan && plan.subquestions) || [];

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

            li.textContent = `${followUp.source} — ${
                followUp.query
            }`;

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

        const sideA = document.createElement("p");

        sideA.textContent =
            `${cite(item.sources_a)}: ${item.side_a || ""}`;

        const sideB = document.createElement("p");

        sideB.textContent =
            `${cite(item.sources_b)}: ${item.side_b || ""}`;

        body.appendChild(heading);
        body.appendChild(sideA);
        body.appendChild(sideB);

        row.appendChild(body);

        contradictionList.appendChild(row);
    }

    contradictionCard.classList.remove("hidden");
}


function cite(ids) {
    if (!Array.isArray(ids) || !ids.length) {
        return "No citation";
    }

    return ids.map((id) => `[${id}]`).join(", ");
}
