(() => {
  document.documentElement.classList.add("js-enabled");
  // A returned page after logout must not expose the previous record editor.
  window.addEventListener("pageshow", (event) => { if (event.persisted) window.location.reload(); });
  if (document.body.dataset.draftOwner === "anonymous") {
    for (const name of ["localStorage", "sessionStorage"]) {
      try {
        const storage = window[name];
        for (let index = storage.length - 1; index >= 0; index -= 1) {
          const key = storage.key(index);
          if (key?.startsWith("dugyeon.record-draft.")) storage.removeItem(key);
        }
        storage.removeItem("dugyeon.record-save-pending");
      } catch { /* Browser storage may be disabled. */ }
    }
  }
  const loginForm = document.querySelector(".login-form");
  if (loginForm) {
    let submitted = false;
    loginForm.addEventListener("submit", (event) => {
      if (submitted) {
        event.preventDefault();
        return;
      }
      if (!loginForm.checkValidity()) return;
      submitted = true;
      const submitButton = loginForm.querySelector("button[type=submit]");
      if (submitButton) {
        submitButton.disabled = true;
        submitButton.textContent = "로그인 중…";
      }
    });
  }
  const dialog = document.getElementById("task-editor-dialog");
  if (!dialog) return;

  const content = dialog.querySelector("[data-task-dialog-content]");
  let opener = null;
  let initialTaskValues = null;

  const taskFormValues = () => {
    const form = content.querySelector("[data-task-dialog-form]");
    if (!form) return null;
    return JSON.stringify(Array.from(form.elements)
      .filter((field) => field.name && !["hidden", "submit", "button"].includes(field.type))
      .map((field) => [field.name, ["checkbox", "radio"].includes(field.type) ? field.checked : field.value]));
  };

  const requestDialogClose = () => {
    if (initialTaskValues !== null && taskFormValues() !== initialTaskValues &&
        !window.confirm("작성 중인 내용을 저장하지 않고 닫을까요?")) return;
    dialog.close();
  };
  let statusToastTimer = 0;
  let draggedItem = null;
  let draggedList = null;
  let originalOrder = [];
  let dropAccepted = false;

  const announceStatus = (message) => {
    const toast = document.getElementById("status-toast");
    if (!toast) return;
    toast.textContent = message;
    toast.hidden = false;
    window.clearTimeout(statusToastTimer);
    statusToastTimer = window.setTimeout(() => {
      toast.hidden = true;
    }, 2200);
  };

  const updateCharacterCounts = (root = document) => {
    root.querySelectorAll("[data-character-count]").forEach((counter) => {
      const field = document.getElementById(counter.dataset.target);
      if (!field) return;
      const update = () => {
        counter.textContent = `${Array.from(field.value).length} / ${counter.dataset.max}`;
      };
      field.addEventListener("input", update);
      update();
    });
  };

  const bindLengthLimits = (root = document) => {
    root.querySelectorAll("[data-max-length]").forEach((field) => {
      const maximum = Number(field.dataset.maxLength);
      const counter = root.querySelector(`[data-character-count][data-target="${field.id}"]`);
      const validate = () => {
        const value = field.name === "title" || field.name === "name" ? field.value.trim() : field.value;
        const length = Array.from(value).length;
        field.setCustomValidity(length > maximum ? `최대 ${maximum}자까지 입력할 수 있습니다.` : "");
        if (counter) counter.textContent = `${length} / ${maximum}`;
      };
      field.addEventListener("input", validate);
      validate();
    });
  };

  const recordDraftKey = (date) => `dugyeon.record-draft.${document.body.dataset.draftOwner}.${date}`;

  const initializeRecordDrafts = (root = document) => {
    root.querySelectorAll("[data-record-draft]").forEach((form) => {
      const key = recordDraftKey(form.dataset.recordDate);
      const fields = Array.from(form.querySelectorAll("textarea"));
      try {
        const legacyKey = `dugyeon.record-draft.${form.dataset.recordDate}`;
        const legacy = localStorage.getItem(legacyKey);
        if (legacy && !sessionStorage.getItem(key)) sessionStorage.setItem(key, legacy);
        localStorage.removeItem(legacyKey);
        const draft = JSON.parse(sessionStorage.getItem(key) || "null");
        if (draft?.values) {
          fields.forEach((field) => {
            if (Object.hasOwn(draft.values, field.name)) field.value = draft.values[field.name];
          });
        }
      } catch { /* Browser storage can be disabled or malformed. */ }

      const persistDraft = () => {
        const values = Object.fromEntries(fields.map((field) => [field.name, field.value]));
        try { sessionStorage.setItem(key, JSON.stringify({ values, savedAt: Date.now() })); } catch { /* Browser storage can be disabled. */ }
      };
      fields.forEach((field) => {
        field.addEventListener("input", persistDraft);
        field.dispatchEvent(new Event("input", { bubbles: true }));
      });
      form.addEventListener("submit", () => {
        persistDraft();
        try { sessionStorage.setItem("dugyeon.record-save-pending", form.dataset.recordDate); } catch { /* Browser storage can be disabled. */ }
      });
    });
  };

  const clearSavedRecordDraft = () => {
    try {
      const pendingDate = sessionStorage.getItem("dugyeon.record-save-pending");
      const notices = Array.from(document.querySelectorAll(".notice"), (notice) => notice.textContent);
      const saved = notices.some((text) => text.includes("개인 기록을 저장했습니다."));
      const deleted = notices.some((text) => text.includes("개인 기록을 삭제했습니다."));
      if (pendingDate && (saved || deleted)) sessionStorage.removeItem(recordDraftKey(pendingDate));
      if (saved || deleted) sessionStorage.removeItem("dugyeon.record-save-pending");
    } catch { /* Browser storage can be disabled. */ }
  };

  const clearLocalRecordDrafts = () => {
    for (const name of ["sessionStorage", "localStorage"]) {
      try {
        const storage = window[name];
        for (let index = storage.length - 1; index >= 0; index -= 1) {
          const key = storage.key(index);
          if (key?.startsWith("dugyeon.record-draft.")) storage.removeItem(key);
        }
        storage.removeItem("dugyeon.record-save-pending");
      } catch { /* Browser storage may be disabled. */ }
    }
  };

  const seoulDate = () => {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Seoul",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(new Date());
    const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    return `${values.year}-${values.month}-${values.day}`;
  };

  const updateDateRollover = () => {
    const notice = document.querySelector("[data-rollover-date]");
    if (notice?.dataset.rolloverDate && notice.dataset.rolloverDate !== seoulDate()) {
      notice.hidden = false;
    }
  };

  const bindAutoGrowTextareas = (root = document) => {
    root.querySelectorAll("textarea[data-auto-grow]").forEach((field) => {
      if (field.dataset.autoGrowBound === "true") return;
      const maximum = Number(field.dataset.autoGrow) || 600;
      const resize = () => {
        field.style.height = "auto";
        const height = Math.min(field.scrollHeight, maximum);
        field.style.height = `${height}px`;
        field.style.overflowY = field.scrollHeight > maximum ? "auto" : "hidden";
      };
      field.dataset.autoGrowBound = "true";
      field.addEventListener("input", resize);
      resize();
    });
  };

  const replaceDayContent = (html, url, addHistory = true) => {
    const parsed = new DOMParser().parseFromString(html, "text/html");
    const replacement = parsed.querySelector("#day-content");
    const current = document.querySelector("#day-content");
    if (!replacement || !current) return false;
    current.replaceWith(replacement);
    if (addHistory) window.history.pushState({}, "", url.toString());
    replacement.querySelector(".day.selected")?.focus();
    revealSelectedWeek();
    replacement.querySelectorAll("[data-status-select]").forEach((select) => {
      select.dataset.savedValue = select.value;
    });
    bindLengthLimits(replacement);
    bindAutoGrowTextareas(replacement);
    initializeRecordDrafts(replacement);
    window.initializePhotoSections?.(replacement);
    window.initializeNoteEditors?.(replacement);
    updateDateRollover();
    return true;
  };

  const revealSelectedWeek = () => {
    const grid = document.querySelector("[data-week-overview] .week-grid");
    const selected = grid?.querySelector(".selected");
    if (selected && grid.scrollWidth > grid.clientWidth) {
      grid.scrollLeft = selected.offsetLeft - grid.offsetLeft - (grid.clientWidth - selected.clientWidth) / 2;
    }
  };

  const refreshWeek = (result) => {
    const current = document.querySelector("[data-week-overview]");
    // A response from the previous day must not replace the week after navigation.
    if (!current || result.week_date !== current.dataset.selectedDate || typeof result.week_html !== "string") return;
    const replacement = new DOMParser().parseFromString(result.week_html, "text/html").querySelector("[data-week-overview]");
    if (!replacement) return;
    const scrollLeft = current.querySelector(".week-grid")?.scrollLeft || 0;
    current.replaceWith(replacement);
    replacement.querySelector(".week-grid").scrollLeft = scrollLeft;
  };

  const updateDay = async (url, addHistory = true) => {
    try {
      const response = await fetch(url, {
        credentials: "same-origin",
        headers: { "HX-Request": "true" },
      });
      if (!response.ok || response.redirected) {
        window.location.assign(response.url || url);
        return;
      }
      if (!replaceDayContent(await response.text(), url, addHistory)) {
        window.location.assign(url);
      }
    } catch {
      window.location.assign(url);
    }
  };

  const saveStatus = async (form) => {
    const select = form.querySelector("[data-status-select]");
    if (select.disabled) return;
    const statusData = new FormData(form);
    const restoreFocus = document.activeElement === select;
    select.disabled = true;
    const feedback = form.querySelector("[data-status-feedback]");
    const previous = select.dataset.savedValue || select.value;
    const button = form.querySelector("button[type=submit]");
    if (button) button.disabled = true;
    if (feedback) feedback.textContent = "저장 중…";
    try {
      const response = await fetch(form.action, {
        method: "POST",
        body: statusData,
        credentials: "same-origin",
        headers: { "HX-Request": "true" },
      });
      if (response.redirected) {
        window.location.assign(response.url);
        return;
      }
      if (!response.ok) throw new Error("status-save-failed");
      const result = await response.json();
      select.value = result.status;
      select.dataset.savedValue = result.status;
      const retryLink = form.closest("[data-task-sort-item]")?.querySelector("[data-task-retry]");
      if (retryLink) retryLink.hidden = !["NOT_DONE", "PARTIAL"].includes(result.status);
      const list = form.closest("[data-task-sort-list]");
      if (list && Array.isArray(result.task_ids)) restoreTaskOrder(list, result.task_ids);
      refreshWeek(result);
      if (feedback) feedback.textContent = "저장됨";
      announceStatus("상태를 저장했습니다.");
    } catch {
      select.value = previous;
      if (feedback) feedback.textContent = "저장 실패. 다시 시도하세요.";
      announceStatus("상태 저장에 실패했습니다. 다시 시도하세요.");
    } finally {
      select.disabled = false;
      if (restoreFocus) select.focus({preventScroll: true});
      if (button) button.disabled = false;
    }
  };

  const restoreTaskOrder = (list, ids) => {
    const items = new Map(Array.from(list.querySelectorAll("[data-task-sort-item]"), (item) => [item.dataset.taskId, item]));
    ids.forEach((id) => {
      const item = items.get(id);
      if (item) list.append(item);
    });
  };

  const saveTaskOrder = async (list, ids, oldOrder) => {
    const formData = new FormData();
    formData.append("task_date", list.dataset.taskDate);
    formData.append("category_id", list.dataset.categoryId);
    const csrfToken = list.querySelector("[name=csrfmiddlewaretoken]")?.value;
    if (csrfToken) formData.append("csrfmiddlewaretoken", csrfToken);
    ids.forEach((id) => formData.append("task_ids", id));
    try {
      const response = await fetch(list.dataset.reorderUrl, {
        method: "POST",
        body: formData,
        credentials: "same-origin",
        headers: { "HX-Request": "true" },
      });
      if (response.redirected || !response.ok) throw new Error("task-reorder-failed");
      const result = await response.json();
      if (!result.ok || !Array.isArray(result.task_ids)) throw new Error("task-reorder-invalid");
      restoreTaskOrder(list, result.task_ids);
      refreshWeek(result);
      announceStatus("업무 순서를 저장했습니다.");
    } catch {
      restoreTaskOrder(list, oldOrder);
      announceStatus("순서 저장에 실패했습니다. 원래 순서로 되돌렸습니다.");
    }
  };

  const renderForm = async (response, fallbackUrl, resetCloseGuard = false) => {
    if (response.redirected && !response.url.includes("fragment=1")) {
      const html = await response.text();
      if (!replaceDayContent(html, response.url)) {
        window.location.assign(response.url);
        return;
      }
      dialog.close();
      announceStatus("저장했습니다.");
      return;
    }
    if (response.status === 409) {
      content.innerHTML = await response.text();
      if (!dialog.open) dialog.showModal();
      return;
    }
    if (!response.ok) {
      window.location.assign(fallbackUrl);
      return;
    }
    content.innerHTML = await response.text();
    if (resetCloseGuard) initialTaskValues = taskFormValues();
    if (!dialog.open) dialog.showModal();
    bindLengthLimits(content);
    content.querySelector("input:not([type=hidden]), select, textarea")?.focus();
  };

  updateCharacterCounts();
  bindLengthLimits();
  bindAutoGrowTextareas();
  initializeRecordDrafts();
  clearSavedRecordDraft();
  updateDateRollover();
  document.querySelectorAll("[data-status-select]").forEach((select) => {
    select.dataset.savedValue = select.value;
  });
  window.setInterval(updateDateRollover, 15000);
  window.addEventListener("popstate", () => updateDay(window.location.href, false));

  document.addEventListener("click", async (event) => {
    const dayLink = event.target.closest("[data-day-link]");
    if (dayLink && document.querySelector("#day-content")) {
      event.preventDefault();
      await updateDay(dayLink.href);
      return;
    }
    const trigger = event.target.closest("[data-task-dialog]");
    if (trigger) {
      event.preventDefault();
      opener = trigger;
      const url = new URL(trigger.href, window.location.href);
      url.searchParams.set("fragment", "1");
      try {
        const response = await fetch(url, { credentials: "same-origin", headers: { "HX-Request": "true" } });
        await renderForm(response, trigger.href, true);
      } catch {
        window.location.assign(trigger.href);
      }
      return;
    }
    if (event.target.closest("[data-dialog-close]")) requestDialogClose();
  });

  document.addEventListener("change", (event) => {
    const select = event.target.closest("[data-status-select]");
    if (select) select.form.requestSubmit();
  });

  document.addEventListener("submit", async (event) => {
    if (event.target.closest("[data-clear-local-drafts]")) clearLocalRecordDrafts();
    const confirmationForm = event.target.closest("[data-confirm]");
    if (confirmationForm && !window.confirm(confirmationForm.dataset.confirm)) {
      event.preventDefault();
      return;
    }
    const statusForm = event.target.closest("[data-status-form]");
    if (statusForm) {
      event.preventDefault();
      await saveStatus(statusForm);
      return;
    }
    const form = event.target.closest("[data-task-dialog-form]");
    if (!form) return;
    event.preventDefault();
    try {
      const response = await fetch(form.action, {
        method: form.method.toUpperCase(),
        body: new FormData(form),
        credentials: "same-origin",
        headers: { "HX-Request": "true" },
      });
      await renderForm(response, form.action);
    } catch {
      HTMLFormElement.prototype.submit.call(form);
    }
  });

  document.addEventListener("dragstart", (event) => {
    const handle = event.target.closest("[data-drag-handle]");
    if (!handle) return;
    draggedItem = handle.closest("[data-task-sort-item]");
    draggedList = handle.closest("[data-task-sort-list]");
    originalOrder = Array.from(draggedList.querySelectorAll("[data-task-sort-item]"), (item) => item.dataset.taskId);
    dropAccepted = false;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", draggedItem.dataset.taskId);
    draggedItem.classList.add("is-dragging");
  });

  document.addEventListener("dragover", (event) => {
    if (!draggedItem || !draggedList) return;
    const list = event.target.closest("[data-task-sort-list]");
    const target = event.target.closest("[data-task-sort-item]");
    if (list !== draggedList) return;
    // Reordering can move the dragged row underneath the pointer. Browsers
    // still need dragover cancelled there (and in list gaps) to allow drop.
    if (!target || target === draggedItem) {
      event.preventDefault();
      return;
    }
    const draggedStatus = draggedItem.querySelector("[data-status-select]");
    const targetStatus = target.querySelector("[data-status-select]");
    if ((draggedStatus.dataset.savedValue || draggedStatus.value) !==
        (targetStatus.dataset.savedValue || targetStatus.value)) return;
    event.preventDefault();
    const after = event.clientY > target.getBoundingClientRect().top + target.offsetHeight / 2;
    list.insertBefore(draggedItem, after ? target.nextSibling : target);
  });

  document.addEventListener("drop", async (event) => {
    if (!draggedItem || !draggedList) return;
    const list = event.target.closest("[data-task-sort-list]");
    if (list !== draggedList) return;
    event.preventDefault();
    dropAccepted = true;
    const savedList = draggedList;
    const oldOrder = originalOrder;
    const ids = Array.from(savedList.querySelectorAll("[data-task-sort-item]"), (item) => item.dataset.taskId);
    savedList.querySelectorAll(".is-dragging").forEach((item) => item.classList.remove("is-dragging"));
    draggedItem = null;
    draggedList = null;
    originalOrder = [];
    await saveTaskOrder(savedList, ids, oldOrder);
  });

  document.addEventListener("dragend", () => {
    if (draggedItem && draggedList && !dropAccepted) restoreTaskOrder(draggedList, originalOrder);
    draggedItem?.classList.remove("is-dragging");
    draggedItem = null;
    draggedList = null;
    originalOrder = [];
    dropAccepted = false;
  });

  // Backdrop clicks must never discard a task draft. Escape uses the same
  // unsaved-change check as the explicit cancel and close buttons.
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    requestDialogClose();
  });
  dialog.addEventListener("close", () => opener?.focus());
  revealSelectedWeek();
})();
