(() => {
  const dialog = document.getElementById("backup-reminder-dialog");
  if (!dialog || typeof dialog.showModal !== "function") return;
  const form = dialog.querySelector("[data-backup-snooze]");
  let pending = false;
  const snooze = async () => {
    if (pending) return;
    pending = true;
    const button = form.querySelector("button");
    button.disabled = true;
    try {
      // The hidden input named "action" shadows the form.action property.
      const response = await fetch(form.getAttribute("action"), {
        method: "POST", credentials: "same-origin", body: new FormData(form),
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });
      if (!response.ok || response.redirected) throw new Error();
      dialog.close();
      document.querySelector(".backup-reminder-notice")?.remove();
    } catch {
      dialog.querySelector("[data-backup-reminder-error]").textContent = "알림을 미루지 못했어요. 다시 시도해 주세요.";
    } finally {
      pending = false;
      button.disabled = false;
    }
  };
  form.addEventListener("submit", (event) => { event.preventDefault(); snooze(); });
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); snooze(); });
  dialog.showModal();
})();
