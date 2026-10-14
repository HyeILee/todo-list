(() => {
  "use strict";
  const page = document.querySelector("[data-passkey-page]");
  if (!page) return;
  const error = page.querySelector("[data-passkey-error]");
  const status = page.querySelector("[data-passkey-status]");
  const csrf = page.querySelector('[name="csrfmiddlewaretoken"]').value;
  const supported = window.isSecureContext && window.PublicKeyCredential && navigator.credentials;
  const buttons = Array.from(page.querySelectorAll("button"));
  const needsPasskey = Array.from(page.querySelectorAll("[data-passkey-auth], [data-needs-passkey]"));
  if (!supported) {
    const notice = page.querySelector("[data-passkey-unsupported]");
    if (notice) notice.hidden = false;
    needsPasskey.forEach(button => { button.disabled = true; });
  }
  const encode = buffer => btoa(String.fromCharCode(...new Uint8Array(buffer))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const decode = value => Uint8Array.from(atob(value.replace(/-/g, "+").replace(/_/g, "/")), char => char.charCodeAt(0)).buffer;
  const options = (value, registration) => {
    const result = {...value, challenge: decode(value.challenge)};
    if (registration) result.user = {...value.user, id: decode(value.user.id)};
    ["allowCredentials", "excludeCredentials"].forEach(name => {
      if (value[name]) result[name] = value[name].map(key => ({...key, id: decode(key.id)}));
    });
    return result;
  };
  const credentialJSON = credential => {
    if (!credential) throw new Error("본인 확인 창에서 다시 진행해 주세요.");
    const response = {clientDataJSON: encode(credential.response.clientDataJSON)};
    ["attestationObject", "authenticatorData", "signature", "userHandle"].forEach(name => {
      if (credential.response[name]) response[name] = encode(credential.response[name]);
    });
    if (credential.response.getTransports) response.transports = credential.response.getTransports();
    return {id: credential.id, rawId: encode(credential.rawId), type: credential.type, response,
      clientExtensionResults: credential.getClientExtensionResults()};
  };
  const post = async (url, data) => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(url, {method: "POST", credentials: "same-origin", signal: controller.signal,
        headers: {"Content-Type": "application/json", "X-CSRFToken": csrf}, body: JSON.stringify(data)});
      if (response.redirected) throw new Error("로그인 시간이 지났습니다. 비밀번호부터 다시 입력해 주세요.");
      const type = response.headers.get("Content-Type") || "";
      if (!type.includes("application/json")) throw new Error("요청을 완료하지 못했습니다. 새로고침 후 다시 시도해 주세요.");
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "본인 확인을 완료하지 못했습니다.");
      return result;
    } finally { clearTimeout(timeout); }
  };
  const showCodes = codes => {
    page.querySelector("[data-recovery-codes]").hidden = false;
    page.querySelector("[data-recovery-code-text]").textContent = codes.join("\n");
    const saved = page.querySelector("[data-codes-saved]");
    if (saved) { saved.checked = false; page.querySelector("[data-passkey-enable]").disabled = true; }
  };
  const run = async operation => {
    error.hidden = true;
    status.textContent = "본인 확인을 진행하고 있습니다.";
    const states = buttons.map(button => button.disabled);
    buttons.forEach(button => { button.disabled = true; });
    try { await operation(); }
    catch (reason) {
      error.textContent = reason.name === "NotAllowedError" ? "본인 확인을 취소했거나 시간이 지났습니다. 다시 시도해 주세요."
        : reason.name === "InvalidStateError" ? "이미 등록한 패스키일 수 있습니다. 다른 기기 또는 기존 패스키를 이용해 주세요."
        : reason.name === "AbortError" ? "연결이 지연되고 있습니다. 잠시 후 다시 시도해 주세요."
        : reason.message || "본인 확인을 완료하지 못했습니다.";
      error.hidden = false;
      status.textContent = "";
    } finally {
      buttons.forEach((button, index) => { button.disabled = states[index]; });
      const saved = page.querySelector("[data-codes-saved]");
      if (saved) page.querySelector("[data-passkey-enable]").disabled = !saved.checked;
    }
  };
  const done = result => {
    status.textContent = "본인 확인을 완료했습니다.";
    if (result.next) window.location.assign(result.next);
  };
  page.querySelector("[data-passkey-auth]")?.addEventListener("click", () => run(async () => {
    if (!supported) throw new Error("이 브라우저에서는 패스키를 사용할 수 없습니다.");
    const password = page.querySelector("#security-password");
    const start = await post(page.dataset.authOptions, {password: password?.value || ""});
    if (password) password.value = "";
    const credential = await navigator.credentials.get({publicKey: options(start.options, false)});
    const result = await post(page.dataset.authVerify, {ceremony: start.ceremony, credential: credentialJSON(credential),
      remember: page.querySelector("[data-remember]")?.checked === true});
    if (result.codes) { showCodes(result.codes); status.textContent = "새 복구 코드를 보관한 뒤 추가 인증을 켜 주세요."; }
    else done(result);
  }));
  page.querySelector("[data-passkey-register]")?.addEventListener("submit", event => {
    event.preventDefault();
    run(async () => {
      if (!supported) throw new Error("이 브라우저에서는 패스키를 사용할 수 없습니다.");
      const password = page.querySelector("#security-password");
      const start = await post(page.dataset.registerOptions, {password: password.value});
      password.value = "";
      const credential = await navigator.credentials.create({publicKey: options(start.options, true)});
      const result = await post(page.dataset.registerVerify, {ceremony: start.ceremony, credential: credentialJSON(credential),
        label: page.querySelector("#passkey-label").value});
      if (result.codes) { showCodes(result.codes); status.textContent = "패스키를 등록했습니다. 복구 코드를 보관한 뒤 추가 인증을 켜 주세요."; }
      else done(result);
    });
  });
  page.querySelector("[data-codes-saved]")?.addEventListener("change", event => {
    page.querySelector("[data-passkey-enable]").disabled = !event.target.checked;
  });
  page.querySelector("[data-passkey-enable]")?.addEventListener("click", () => run(async () => {
    done(await post(page.dataset.action, {action: "enable", codes_saved: page.querySelector("[data-codes-saved]").checked,
      remember: page.querySelector("[data-remember]").checked}));
  }));
  page.querySelectorAll("[data-security-action]").forEach(button => button.addEventListener("click", () => {
    const action = button.dataset.securityAction;
    const message = action === "disable" ? "추가 인증을 끄고 등록한 패스키와 복구 코드를 해제할까요?"
      : action === "forget" ? "기억한 브라우저를 모두 해제할까요? 다음 로그인에서 패스키로 확인합니다."
      : "새 복구 코드를 만들까요? 기존 복구 코드는 사용할 수 없게 됩니다.";
    if (!window.confirm(message)) return;
    run(async () => {
      const password = page.querySelector("#security-password");
      const result = await post(page.dataset.action, {action, password: password.value});
      password.value = "";
      if (result.codes) { showCodes(result.codes); status.textContent = "새 복구 코드를 안전하게 보관해 주세요."; }
      else done(result);
    });
  }));
  page.querySelector("[data-passkey-recovery]")?.addEventListener("submit", event => {
    event.preventDefault();
    run(async () => {
      const code = page.querySelector("#recovery-code");
      const result = await post(page.dataset.recovery, {code: code.value});
      code.value = "";
      done(result);
    });
  });
})();
