(() => {
  const STYLE_MARKERS = new Set([
    "[u]", "[color=default]", "[color=blue]", "[color=green]", "[color=rose]",
    "[size=small]", "[size=normal]", "[size=large]"
  ]);

  function formatSelection(value, start, end, kind, choice, maximum) {
    if (kind === "bold") return toggleBold(value, start, end, maximum);
    if (start === end) return {error: "꾸밀 문장을 먼저 선택해 주세요."};
    if (!['u', 'color', 'size'].includes(kind)) return {error: "서식을 확인해 주세요."};
    const open = kind === "u" ? "[u]" : `[${kind}=${choice}]`;
    if (!STYLE_MARKERS.has(open)) return {error: "서식을 확인해 주세요."};
    const close = `[/${kind}]`, selected = value.slice(start, end);
    const opening = kind === "u" ? /^\[u\]/ : new RegExp(`^\\[${kind}=(?:${kind === "color" ? "default|blue|green|rose" : "small|normal|large"})\\]`);
    let from = start, to = end, content = selected;
    const before = value.slice(0, start).match(kind === "u" ? /\[u\]$/ : new RegExp(`\\[${kind}=(?:${kind === "color" ? "default|blue|green|rose" : "small|normal|large"})\\]$`));
    if (before && value.slice(end, end + close.length) === close) {
      from -= before[0].length;
      to += close.length;
      content = before[0] + selected + close;
    }
    const lines = content.split("\n");
    const removeUnderline = kind === "u" && lines.every(line => !line.trim() || /^\s*\[u\].*\[\/u\]\s*$/.test(line));
    const replacement = lines.map(line => {
      if (!line.trim()) return line;
      const match = /^(\s*)(.*?)(\s*)$/.exec(line);
      let inner = match[2];
      const old = inner.match(opening);
      if (old && inner.endsWith(close)) inner = inner.slice(old[0].length, -close.length);
      return match[1] + (removeUnderline ? inner : open + inner + close) + match[3];
    }).join("\n");
    const next = value.slice(0, from) + replacement + value.slice(to);
    if (Array.from(next).length > maximum) return {error: `서식을 적용하면 최대 ${maximum}자를 넘습니다. 내용을 조금 줄여 주세요.`};
    return {value: next, start: from, end: from + replacement.length, replacement, from, to};
  }

  function toggleBold(value, start, end, maximum) {
    if (start === end) return { error: "굵게 표시할 문장을 먼저 선택해 주세요." };
    const selected = value.slice(start, end);
    let replacement, from = start, to = end, selectionStart, selectionEnd;
    if (selected.split("\n").every(line => !line.trim() || /^\s*\*\*.+\*\*\s*$/.test(line))) {
      replacement = selected.split("\n").map(line => line.replace(/^(\s*)\*\*(.+)\*\*(\s*)$/, "$1$2$3")).join("\n");
      selectionStart = start;
      selectionEnd = start + replacement.length;
    } else if (value.slice(start - 2, start) === "**" && value.slice(end, end + 2) === "**") {
      from -= 2;
      to += 2;
      replacement = selected;
      selectionStart = from;
      selectionEnd = from + replacement.length;
    } else {
      replacement = selected.split("\n").map(line => line.trim() ? line.replace(/^(\s*)(.*?)(\s*)$/, "$1**$2**$3") : line).join("\n");
      if (replacement === selected) return { error: "굵게 표시할 문장을 먼저 선택해 주세요." };
      selectionStart = start;
      selectionEnd = start + replacement.length;
    }
    const next = value.slice(0, from) + replacement + value.slice(to);
    if (Array.from(next).length > maximum) return { error: `굵게 표시하면 최대 ${maximum}자를 넘습니다. 내용을 조금 줄여 주세요.` };
    return { value: next, start: selectionStart, end: selectionEnd, replacement, from, to };
  }

  function initializeNoteEditors(root = document) {
    root.querySelectorAll("textarea[data-note-editor]").forEach(field => {
      if (field.dataset.noteBound) return;
      field.dataset.noteBound = "true";
      const wrapper = field.closest(".field");
      const heading = wrapper.querySelector(".field-heading");
      const tools = document.createElement("div");
      tools.className = "note-tools";
      const bold = document.createElement("button");
      bold.type = "button";
      bold.className = "note-bold";
      bold.textContent = "B";
      bold.title = "선택한 문장을 굵게 · Ctrl+B";
      bold.setAttribute("aria-label", `${field.labels[0]?.textContent || "기록"} 굵게`);
      const underline = document.createElement("button");
      underline.type = "button";
      underline.className = "note-underline-button";
      underline.textContent = "U";
      underline.title = "선택한 문장에 밑줄 · Ctrl+U";
      underline.setAttribute("aria-label", `${field.labels[0]?.textContent || "기록"} 밑줄`);
      const makeSelect = (label, choices) => {
        const select = document.createElement("select");
        select.setAttribute("aria-label", `${field.labels[0]?.textContent || "기록"} ${label}`);
        select.title = `선택한 문장의 ${label}`;
        [["", label], ...choices].forEach(([value, text]) => {
          const option = document.createElement("option");
          option.value = value;
          option.textContent = text;
          select.append(option);
        });
        return select;
      };
      const size = makeSelect("글자 크기", [["small", "작게 (13)"], ["normal", "기본 (16)"], ["large", "크게 (20)"]]);
      const color = makeSelect("글자 색상", [["default", "검정 (기본)"], ["blue", "파랑"], ["green", "초록"], ["rose", "자주"]]);
      const feedback = document.createElement("small");
      feedback.className = "note-feedback";
      feedback.setAttribute("role", "status");
      feedback.hidden = true;
      tools.append(bold, underline, size, color);
      if (heading) heading.insertBefore(tools, heading.querySelector(".inline-help"));
      else field.before(tools);
      field.after(feedback);
      const applyFormat = (kind, choice) => {
        const next = formatSelection(field.value, field.selectionStart, field.selectionEnd, kind, choice, Number(field.dataset.maxLength) || 2000);
        feedback.hidden = !next.error;
        feedback.textContent = next.error || "";
        if (next.error) { field.focus(); return; }
        field.focus();
        field.setRangeText(next.replacement, next.from, next.to, "select");
        field.setSelectionRange(next.start, next.end);
        field.dispatchEvent(new Event("input", { bubbles: true }));
      };
      // Keep the textarea selection when clicking B.
      bold.addEventListener("mousedown", event => event.preventDefault());
      bold.addEventListener("click", () => applyFormat("bold"));
      underline.addEventListener("mousedown", event => event.preventDefault());
      underline.addEventListener("click", () => applyFormat("u"));
      size.addEventListener("change", () => { if (size.value) applyFormat("size", size.value); size.value = ""; });
      color.addEventListener("change", () => { if (color.value) applyFormat("color", color.value); color.value = ""; });
      field.addEventListener("keydown", event => {
        if ((event.ctrlKey || event.metaKey) && ["b", "u"].includes(event.key.toLowerCase())) {
          event.preventDefault();
          applyFormat(event.key.toLowerCase() === "b" ? "bold" : "u");
        }
      });
      field.addEventListener("input", () => { feedback.hidden = true; });
    });
  }

  function closeHelp(help, dismissed = false) {
    help.classList.remove("is-open");
    help.classList.toggle("is-dismissed", dismissed);
    help.querySelector("[data-help-toggle]").setAttribute("aria-expanded", "false");
  }
  document.addEventListener("click", event => {
    const toggle = event.target.closest("[data-help-toggle]");
    document.querySelectorAll(".inline-help.is-open").forEach(help => {
      if (help !== toggle?.closest(".inline-help")) closeHelp(help, true);
    });
    if (!toggle) return;
    const help = toggle.closest(".inline-help");
    const open = !help.classList.contains("is-open");
    help.classList.toggle("is-open", open);
    help.classList.toggle("is-dismissed", !open);
    toggle.setAttribute("aria-expanded", String(open));
  });
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    document.querySelectorAll(".inline-help").forEach(help => closeHelp(help, true));
  });
  document.addEventListener("pointerover", event => event.target.closest(".inline-help")?.classList.remove("is-dismissed"));
  document.addEventListener("focusin", event => event.target.closest(".inline-help")?.classList.remove("is-dismissed"));
  window.initializeNoteEditors = initializeNoteEditors;
  initializeNoteEditors();
  if (typeof module !== "undefined") module.exports = { toggleBold, formatSelection };
})();
