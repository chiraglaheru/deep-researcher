/*
 * Minimal markdown renderer for the research report.
 *
 * Written by hand rather than pulled from a CDN so the app stays offline-capable
 * and adds no third-party script to a page that displays model output.
 *
 * Everything is HTML-escaped first and only then given structure, so model
 * output can never inject markup. HTML tags in the source are shown as text
 * rather than rendered, and link hrefs are restricted to non-scripting schemes.
 */

function escapeHtml(text) {
    return String(text == null ? "" : text)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function safeUrl(url) {
    const value = String(url || "").trim();
    // Only schemes that cannot execute script.
    if (/^(https?:|mailto:|#)/i.test(value)) {
        return value;
    }
    return "";
}

function slugifyHeading(text) {
    return String(text || "")
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .slice(0, 60);
}

function citationLink(number) {
    return '<a class="citation" href="#ref-' + number + '" data-ref="' + number +
        '" title="Go to reference ' + number + '">[' + number + "]</a>";
}

function renderInline(text) {
    // Pull autolinks out before escaping, otherwise "<https://x>" becomes
    // "&lt;https://x&gt;" and the reference is no longer clickable.
    const links = [];
    let raw = String(text == null ? "" : text).replace(
        /<(https?:\/\/[^>\s]+)>/g, function (match, url) {
            links.push(url);
            return "@@LINK" + (links.length - 1) + "@@";
        });

    let out = escapeHtml(raw);

    // Pull inline code out first so its contents are never treated as emphasis.
    const codes = [];
    out = out.replace(/`([^`]+)`/g, function (match, code) {
        codes.push(code);
        return "@@CODE" + (codes.length - 1) + "@@";
    });

    // [3][7] is one citation pair; a bare [3] is a single citation. Both link
    // to the matching entry in the References section.
    //
    // The lookbehind matters: "a[0]" is array indexing, not a citation, and
    // linking it would produce a dangling #ref-0 anchor.
    // Citations are pulled to placeholders first so the [n] inside the
    // generated <a> tag is not re-matched by the single-citation pass.
    const cites = [];
    out = out.replace(/(?<![\w\]])\[(\d{1,3})\]\s*\[(\d{1,3})\]/g,
        function (m, a, b) {
            cites.push(a);
            const first = "@@CITE" + (cites.length - 1) + "@@";
            cites.push(b);
            return first + "@@CITE" + (cites.length - 1) + "@@";
        });
    out = out.replace(/(?<![\w\]])\[(\d{1,3})\](?!\()/g, function (m, n) {
        cites.push(n);
        return "@@CITE" + (cites.length - 1) + "@@";
    });

    out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (match, label, href) {
        const url = safeUrl(href);
        if (!url) {
            return label;
        }
        return '<a href="' + escapeHtml(url) +
            '" target="_blank" rel="noopener noreferrer">' + label + "</a>";
    });

    out = out.replace(/(^|[\s(>])((?:https?:\/\/)[^\s<)]+)/g,
        function (match, lead, url) {
            const clean = safeUrl(url);
            if (!clean) {
                return match;
            }
            return lead + '<a href="' + escapeHtml(clean) +
                '" target="_blank" rel="noopener noreferrer">' +
                escapeHtml(clean) + "</a>";
        });

    out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    out = out.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
    out = out.replace(/~~([^~]+)~~/g, "<del>$1</del>");

    out = out.replace(/@@CITE(\d+)@@/g, function (m, i) {
        return citationLink(cites[Number(i)]);
    });

    out = out.replace(/@@CODE(\d+)@@/g, function (m, i) {
        return "<code>" + codes[Number(i)] + "</code>";
    });

    return out.replace(/@@LINK(\d+)@@/g, function (m, i) {
        const url = links[Number(i)];
        return '<a href="' + escapeHtml(url) +
            '" target="_blank" rel="noopener noreferrer">' + escapeHtml(url) + "</a>";
    });
}

function tableCells(line) {
    return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|");
}

function isTableDivider(line) {
    return line.indexOf("-") !== -1 && /^[|\s:-]+$/.test(line);
}

function renderMarkdown(markdown) {
    if (!markdown) {
        return "";
    }

    const lines = String(markdown).replace(/\r\n/g, "\n").split("\n");
    const html = [];
    let paragraph = [];
    let list = null;            // "ul" | "ol" | null
    let inCode = false;
    let codeLines = [];
    let tableRows = [];
    let inReferences = false;
    let refOpen = false;
    let pendingRef = null;

    function flushParagraph() {
        if (paragraph.length) {
            html.push("<p>" + renderInline(paragraph.join(" ")) + "</p>");
            paragraph = [];
        }
    }

    function flushList() {
        if (list) {
            html.push("</" + list + ">");
            list = null;
        }
    }

    function flushRef() {
        if (!pendingRef) {
            return;
        }
        if (!refOpen) {
            html.push("<ol class='reference-list'>");
            refOpen = true;
        }
        html.push('<li id="ref-' + pendingRef.number + '">' +
            pendingRef.parts.join(" ") + "</li>");
        pendingRef = null;
    }

    function leaveReferences() {
        flushRef();
        if (refOpen) {
            html.push("</ol>");
            refOpen = false;
        }
        inReferences = false;
    }

    function flushTable() {
        if (!tableRows.length) {
            return;
        }
        const rows = tableRows.filter(function (row) {
            return !row.isDivider;
        });
        const body = (tableRows[0] && tableRows[0].isDivider) ? rows : rows.slice(1);
        const headerCells = rows.length ? rows[0].cells : [];
        html.push(
            "<table><thead><tr>" +
            headerCells.map(function (c) {
                return "<th>" + renderInline(c) + "</th>";
            }).join("") +
            "</tr></thead><tbody>" +
            body.map(function (row) {
                return "<tr>" + row.cells.map(function (c) {
                    return "<td>" + renderInline(c) + "</td>";
                }).join("") + "</tr>";
            }).join("") +
            "</tbody></table>"
        );
        tableRows = [];
    }

    function flushAll() {
        flushParagraph();
        flushList();
        flushTable();
    }

    for (const line of lines) {
        if (/^\s*```/.test(line)) {
            if (inCode) {
                html.push("<pre><code>" + escapeHtml(codeLines.join("\n")) +
                    "</code></pre>");
                codeLines = [];
                inCode = false;
            } else {
                flushAll();
                inCode = true;
            }
            continue;
        }
        if (inCode) {
            codeLines.push(line);
            continue;
        }

        if (/^\s*\|/.test(line)) {
            flushParagraph();
            flushList();
            tableRows.push({
                cells: tableCells(line).map(function (c) {
                    return c.trim();
                }),
                isDivider: isTableDivider(line),
            });
            continue;
        }
        flushTable();

        const heading = /^(#{1,6})\s+(.*\S)\s*$/.exec(line);
        if (heading) {
            if (inReferences) {
                leaveReferences();
            }
            flushAll();
            const level = Math.min(heading[1].length + 1, 6);
            const text = heading[2];
            const id = slugifyHeading(text);
            if (/^references$/i.test(text.trim())) {
                // The list is opened lazily by the first entry, after this
                // heading has been emitted.
                inReferences = true;
                refOpen = false;
                pendingRef = null;
            }
            html.push("<h" + level + ' id="' + escapeHtml(id) + '">' +
                renderInline(text) + "</h" + level + ">");
            continue;
        }

        if (/^\s*([-*_])\s*(\1\s*){2,}$/.test(line)) {
            flushAll();
            html.push("<hr>");
            continue;
        }

        if (/^\s*&gt;\s?|^\s*>\s?/.test(line)) {
            flushAll();
            html.push("<blockquote>" +
                renderInline(line.replace(/^\s*&gt;\s?|^\s*>\s?/, "")) +
                "</blockquote>");
            continue;
        }

        // In the References section a numbered entry begins a reference, and
        // its indented continuation lines belong to it. Handling this before the
        // generic list branch is what keeps every entry in one <ol> -- otherwise
        // each continuation line closes the list and the next entry opens a new
        // one, so the browser renumbers every reference "1.".
        const refEntry = /^\s*(\d{1,3})\.\s+(.*)$/.exec(line);
        if (inReferences && refEntry) {
            flushParagraph();
            flushList();
            flushTable();
            flushRef();
            pendingRef = { number: refEntry[1], parts: [renderInline(refEntry[2])] };
            continue;
        }
        if (inReferences && pendingRef && line.trim() && !/^\s*[-*+|]/.test(line)) {
            pendingRef.parts.push(renderInline(line.trim()));
            continue;
        }
        if (inReferences && pendingRef && !line.trim()) {
            flushRef();
            continue;
        }

        const bullet = /^\s*[-*+]\s+(.*)$/.exec(line);
        const ordered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
        if (bullet || ordered) {
            flushParagraph();
            flushTable();
            const kind = bullet ? "ul" : "ol";
            if (list !== kind) {
                flushList();
                html.push("<" + kind + ">");
                list = kind;
            }
            html.push("<li>" + renderInline((bullet || ordered)[1]) + "</li>");
            continue;
        }

        if (!line.trim()) {
            flushAll();
            continue;
        }

        flushList();
        paragraph.push(line.trim());
    }

    if (inCode && codeLines.length) {
        html.push("<pre><code>" + escapeHtml(codeLines.join("\n")) + "</code></pre>");
    }
    if (inReferences) {
        leaveReferences();
    }
    flushAll();

    return html.join("\n");
}

window.renderMarkdown = renderMarkdown;
window.escapeHtml = escapeHtml;