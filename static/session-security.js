(() => {
  const script = document.currentScript;
  const form = document.querySelector("form[data-clear-local-drafts]");
  if (!script || !form) return;
  const idleMs = Number(script.dataset.idleSeconds) * 1000;
  const csrf = form.querySelector("[name=csrfmiddlewaretoken]")?.value;
  if (!idleMs || !csrf) return;
  let lastActivity = Date.now();
  let lastHeartbeat = lastActivity;
  let pending = false;
  let expired = false;
  let loggingOut = false;
  let channel;
  try { channel = new BroadcastChannel(`dugyeon-session-${document.body.dataset.draftOwner}`); } catch { /* Optional in older browsers. */ }
  function checkIdle() {
    if (Date.now() - lastActivity < idleMs) return false;
    if (!loggingOut) {
      loggingOut = true;
      // requestSubmit also invokes the existing draft-clearing submit handler.
      form.requestSubmit();
    }
    return true;
  }
  async function activity(event) {
    if (event && !event.isTrusted) return;
    if (checkIdle()) return;
    lastActivity = Date.now();
    channel?.postMessage({ type: "activity", at: lastActivity });
    if (expired || pending || lastActivity - lastHeartbeat < 5 * 60 * 1000) return;
    pending = true;
    lastHeartbeat = lastActivity;
    try {
      const response = await fetch(script.dataset.activityUrl, {
        method: "POST", credentials: "same-origin", redirect: "error",
        headers: { "X-CSRFToken": csrf },
      });
      if (response.status === 401 || response.status === 403) {
        expired = true;
        const toast = document.getElementById("status-toast");
        if (toast) {
          toast.textContent = "로그인이 만료됐어요. 작성 중인 내용을 복사한 뒤 다시 로그인해 주세요.";
          toast.hidden = false;
        }
      } else if (!response.ok) {
        lastHeartbeat = Date.now() - 4 * 60 * 1000;
      }
    } catch { lastHeartbeat = Date.now() - 4 * 60 * 1000; } // Retry after one minute without discarding active writing.
    finally { pending = false; }
  }
  if (channel) {
    channel.onmessage = ({ data }) => {
      if (data?.type === "activity" && Number.isFinite(data.at) && data.at <= Date.now()) {
        lastActivity = Math.max(lastActivity, data.at);
      }
    };
    channel.postMessage({ type: "activity", at: lastActivity });
  }
  for (const name of ["pointerdown", "keydown", "input", "scroll"]) document.addEventListener(name, activity, { passive: true });
  window.addEventListener("focus", () => checkIdle());
  document.addEventListener("visibilitychange", () => { if (!document.hidden) checkIdle(); });
  setInterval(checkIdle, 15 * 1000);
})();
