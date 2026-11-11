(() => {
  const MAX_UPLOAD_BYTES = 2_400_000;

  function loadImage(file) {
    return new Promise((resolve, reject) => {
      // data: images are permitted by our CSP; blob: image URLs are not.
      const reader = new FileReader();
      const image = new Image();
      const unreadable = () => reject(new Error("사진을 읽을 수 없습니다. 파일을 다시 선택해 주세요."));
      image.onload = () => resolve(image);
      image.onerror = unreadable;
      reader.onerror = unreadable;
      reader.onabort = unreadable;
      reader.onload = () => { image.src = reader.result; };
      reader.readAsDataURL(file);
    });
  }

  function canvasBlob(canvas, type, quality) {
    return new Promise((resolve) => canvas.toBlob(resolve, type, quality));
  }

  async function optimizePhoto(file) {
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) throw new Error("JPEG, PNG, WebP 사진만 올릴 수 있습니다.");
    if (file.size <= 1_800_000) return file;
    const image = await loadImage(file);
    let scale = Math.min(1, 1920 / Math.max(image.naturalWidth, image.naturalHeight));
    for (let attempt = 0; attempt < 5; attempt += 1) {
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
      canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
      canvas.getContext("2d", { alpha: true }).drawImage(image, 0, 0, canvas.width, canvas.height);
      const blob = await canvasBlob(canvas, "image/webp", Math.max(0.58, 0.88 - attempt * 0.07));
      if (blob && blob.size <= MAX_UPLOAD_BYTES) {
        const base = file.name.replace(/\.[^.]+$/, "").slice(0, 180) || "photo";
        return new File([blob], `${base}.webp`, { type: "image/webp", lastModified: file.lastModified });
      }
      scale *= 0.78;
    }
    throw new Error("사진 용량을 충분히 줄이지 못했습니다.");
  }

  function addPhotoCard(grid, photo, csrf) {
    const figure = document.createElement("figure");
    const image = document.createElement("img");
    const caption = document.createElement("figcaption");
    image.src = photo.content_url;
    image.alt = photo.name;
    image.loading = "lazy";
    const category = document.createElement("span");
    category.className = `photo-category photo-category-${photo.category.toLowerCase()}`;
    category.textContent = photo.category_label;
    const name = document.createElement("span");
    name.className = "photo-name";
    name.textContent = photo.name;
    caption.append(category, name);
    const form = document.createElement("form");
    form.className = "photo-card-actions";
    form.method = "post";
    form.action = photo.delete_url;
    form.dataset.photoDeleteForm = "";
    form.dataset.photoName = photo.name;
    const token = document.createElement("input");
    token.type = "hidden";
    token.name = "csrfmiddlewaretoken";
    token.value = csrf;
    const button = document.createElement("button");
    button.className = "photo-delete-button";
    button.type = "submit";
    button.textContent = "삭제";
    button.setAttribute("aria-label", `${photo.name} 사진 삭제`);
    form.append(token, button);
    initializePhotoDeletion(form);
    figure.append(image, caption, form);
    grid.append(figure);
  }

  // Keep the old form working during a rolling static-file update.
  function initializeLegacyUploader(section) {
    const form = section.querySelector("[data-photo-upload-form]");
    if (!form) return;
    if (form.dataset.uploadInitialized) return;
    form.dataset.uploadInitialized = "true";
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (section.dataset.photoBusy) return;
      const input = form.querySelector("[data-photo-input]");
      const category = form.querySelector("[data-photo-category]");
      const status = form.querySelector("[data-photo-status]");
      const submit = form.querySelector("button[type=submit]");
      const countNode = section.querySelector("[data-photo-count]");
      const grid = section.querySelector("[data-photo-grid]");
      const files = Array.from(input.files || []);
      const current = grid.querySelectorAll("figure").length;
      if (!files.length) { status.textContent = "올릴 사진을 선택해 주세요."; return; }
      if (current + files.length > 10) { status.textContent = `하루 10장까지만 올릴 수 있습니다. 지금은 ${10 - current}장을 더 올릴 수 있습니다.`; return; }
      section.dataset.photoBusy = "true";
      input.disabled = true;
      submit.disabled = true;
      const csrf = form.querySelector("input[name=csrfmiddlewaretoken]").value;
      let uploaded = 0;
      try {
        for (const original of files) {
          status.textContent = `${files.length}장 중 ${uploaded + 1}번째 사진을 준비하는 중입니다…`;
          const file = await optimizePhoto(original);
          const data = new FormData();
          data.append("photo", file, file.name);
          data.append("category", category.value);
          const response = await fetch(form.action, { method: "POST", headers: { "X-CSRFToken": csrf, "X-Requested-With": "XMLHttpRequest" }, body: data, credentials: "same-origin" });
          const result = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(result.error || "사진을 올리지 못했습니다.");
          addPhotoCard(grid, result, csrf);
          uploaded += 1;
          countNode.textContent = `${result.count} / 10`;
        }
        const remaining = 10 - (current + uploaded);
        status.textContent = `사진 ${uploaded}장을 올렸습니다.`;
        input.value = "";
        if (remaining > 0) { input.disabled = false; submit.disabled = false; }
      } catch (error) {
        status.textContent = error.message;
        input.disabled = false;
        submit.disabled = false;
      } finally {
        delete section.dataset.photoBusy;
        const full = grid.querySelectorAll("figure").length >= 10;
        input.disabled = full;
        submit.disabled = full;
      }
    });
  }

  function initializeUploader(section) {
    const form = section.querySelector("[data-photo-upload-form]");
    if (!form) return;
    const list = form.querySelector("[data-photo-queue]");
    if (!list) { initializeLegacyUploader(section); return; }
    if (form.dataset.uploadInitialized) return;
    form.dataset.uploadInitialized = "true";
    const input = form.querySelector("[data-photo-input]");
    const category = form.querySelector("[data-photo-category]");
    const status = form.querySelector("[data-photo-status]");
    const submit = form.querySelector("button[type=submit]");
    const retry = form.querySelector("[data-photo-retry]");
    const queueCount = form.querySelector("[data-queue-count]");
    const capacity = form.querySelector("[data-photo-capacity]");
    const choose = section.querySelector("[data-photo-choose]");
    const countNode = section.querySelector("[data-photo-count]");
    const grid = section.querySelector("[data-photo-grid]");
    const limit = Number(section.dataset.photoLimit) || 10;
    const csrf = form.querySelector("input[name=csrfmiddlewaretoken]").value;
    const queue = [];
    let preparing = 0;
    const element = (tag, className, text) => {
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined) node.textContent = text;
      return node;
    };
    const photoCount = () => grid.querySelectorAll("figure").length;
    const busy = () => Boolean(section.dataset.photoBusy);
    const render = () => {
      const ready = queue.filter(item => item.state === "ready").length;
      const failed = queue.filter(item => item.state === "failed").length;
      const locked = busy();
      section.dataset.photoPending = queue.length || preparing ? "true" : "";
      list.replaceChildren();
      for (const item of queue) {
        const row = element("div", "photo-queue-item");
        const image = element("img", "photo-queue-thumb");
        if (item.preview) image.src = item.preview;
        image.alt = `${item.file.name} 미리보기`;
        const copy = element("div", "photo-queue-copy");
        const name = element("span", "photo-queue-name", item.file.name);
        name.title = item.file.name;
        const message = item.state === "failed" ? item.error : {
          reading: "사진을 확인하는 중…", ready: "올릴 준비가 됐어요",
          preparing: "용량을 줄이는 중…", uploading: "사진을 올리는 중…",
        }[item.state];
        const state = element("span", `photo-queue-state${item.state === "failed" ? " is-failed" : ""}`, message);
        copy.append(name, state);
        if (["preparing", "uploading"].includes(item.state)) {
          const progress = element("div", "photo-progress");
          progress.setAttribute("role", "progressbar");
          progress.setAttribute("aria-label", `${item.file.name} 처리 중`);
          progress.setAttribute("aria-valuetext", message);
          copy.append(progress);
        }
        const select = element("select", "photo-queue-category");
        select.setAttribute("aria-label", `${item.file.name} 사진 분류`);
        for (const [value, label] of [["LIFE", "Life"], ["WORK", "Work"], ["ETC", "Etc"]]) {
          const option = element("option", "", label); option.value = value; select.append(option);
        }
        select.value = item.category; select.disabled = locked;
        select.addEventListener("change", () => { if (!busy()) item.category = select.value; });
        const remove = element("button", "photo-queue-remove", "×");
        remove.type = "button"; remove.disabled = locked;
        remove.setAttribute("aria-label", `${item.file.name} 선택 취소`);
        remove.addEventListener("click", () => {
          if (busy()) return;
          queue.splice(queue.indexOf(item), 1); render();
        });
        row.append(image, copy, select, remove); list.append(row);
      }
      if (!queue.length) list.append(element("p", "photo-queue-empty", "사진을 추가하면 여기에서 먼저 확인할 수 있어요."));
      queueCount.textContent = `${queue.length}장`;
      capacity.textContent = `오늘 ${photoCount()} / ${limit}장`;
      const full = photoCount() + queue.length >= limit;
      input.disabled = locked || full; category.disabled = locked;
      if (choose) choose.setAttribute("aria-disabled", String(input.disabled));
      submit.disabled = locked || preparing > 0 || !ready;
      submit.hidden = !locked && !ready && failed > 0;
      submit.textContent = locked ? "올리는 중…" : ready ? `${ready}장 올리기` : "사진 올리기";
      retry.hidden = !failed; retry.disabled = locked || preparing > 0;
      retry.classList.toggle("primary", !ready);
      retry.classList.toggle("quiet", Boolean(ready));
      if (!queue.length && photoCount() >= limit) status.textContent = "오늘의 사진을 모두 올렸습니다. 사진을 삭제하거나 다른 날짜를 선택해 주세요.";
    };
    section.refreshPhotoUploader = render;
    const addFiles = async files => {
      if (busy()) return;
      const available = Math.max(0, limit - photoCount() - queue.length);
      const selected = Array.from(files);
      const allowed = selected.filter(file => ["image/jpeg", "image/png", "image/webp"].includes(file.type));
      const warnings = [];
      if (allowed.length !== selected.length) warnings.push("JPEG, PNG, WebP 사진을 선택해 주세요.");
      if (allowed.length > available) warnings.push(`오늘 더 선택할 수 있는 사진은 ${available}장입니다.`);
      const batch = allowed.slice(0, available).map(file => ({file, category:category.value, state:"reading", preview:"", error:""}));
      queue.push(...batch); preparing += batch.length;
      status.textContent = warnings.join(" "); render();
      await Promise.all(batch.map(item => new Promise(resolve => {
        const reader = new FileReader();
        const finish = (preview, error) => {
          item.preview = preview; item.state = error ? "failed" : "ready"; item.error = error || "";
          preparing -= 1; render(); resolve();
        };
        reader.onload = () => finish(reader.result, "");
        reader.onerror = reader.onabort = () => finish("", "사진을 읽을 수 없어요. 선택을 취소하고 다시 추가해 주세요.");
        reader.readAsDataURL(item.file);
      })));
    };
    input.addEventListener("change", async () => { const files = Array.from(input.files || []); input.value = ""; await addFiles(files); });
    choose?.addEventListener("keydown", event => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); if (!input.disabled) input.click(); } });
    for (const name of ["dragenter", "dragover"]) form.addEventListener(name, event => { event.preventDefault(); if (!busy()) form.classList.add("is-dragging"); });
    form.addEventListener("dragleave", event => { if (!form.contains(event.relatedTarget)) form.classList.remove("is-dragging"); });
    form.addEventListener("drop", event => { event.preventDefault(); form.classList.remove("is-dragging"); addFiles(event.dataTransfer.files); });
    const upload = async failedOnly => {
      if (busy() || preparing) return;
      const batch = queue.filter(item => item.state === (failedOnly ? "failed" : "ready"));
      if (!batch.length) return;
      section.dataset.photoBusy = "true";
      let uploaded = 0;
      for (const item of batch) {
        try {
          item.state = "preparing"; item.error = ""; render();
          const file = await optimizePhoto(item.file);
          const data = new FormData(); data.append("photo", file, file.name); data.append("category", item.category);
          item.state = "uploading"; render();
          const response = await fetch(form.action, {method:"POST", headers:{"X-CSRFToken":csrf, "X-Requested-With":"XMLHttpRequest"}, body:data, credentials:"same-origin"});
          if (response.redirected) throw new Error("로그인 상태를 확인한 뒤 다시 시도해 주세요.");
          const result = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(result.error || "사진을 올리지 못했어요. 다시 시도해 주세요.");
          if (!result.content_url || !result.delete_url) throw new Error("사진 저장 결과를 확인하지 못했어요. 새로고침 후 확인해 주세요.");
          addPhotoCard(grid, result, csrf); uploaded += 1;
          if (countNode) countNode.textContent = `${result.count} / ${limit}`;
          // Remove only acknowledged successes; retries never include them.
          queue.splice(queue.indexOf(item), 1);
        } catch (error) { item.state = "failed"; item.error = error.message || "사진을 올리지 못했어요."; }
        render();
      }
      delete section.dataset.photoBusy;
      const failures = queue.filter(item => item.state === "failed").length;
      status.textContent = failures ? `${uploaded ? `${uploaded}장은 등록했어요. ` : ""}실패한 ${failures}장만 다시 시도할 수 있어요.` : `사진 ${uploaded}장을 올렸습니다.`;
      render();
    };
    form.addEventListener("submit", event => { event.preventDefault(); upload(false); });
    retry.addEventListener("click", () => upload(true));
    render();
  }

  function initializePhotoDeletion(form) {
    if (form.dataset.deleteInitialized) return;
    form.dataset.deleteInitialized = "true";
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const section = form.closest("[data-photo-section], [data-photo-album]");
      const status = section.querySelector("[data-photo-delete-status]");
      if (section.dataset.photoBusy) {
        status.textContent = "사진을 처리하는 중입니다. 잠시 후 다시 눌러 주세요.";
        return;
      }
      if (!window.confirm(`「${form.dataset.photoName}」 사진을 삭제할까요?\n이날의 사진과 사진첩에서 함께 삭제되며 복구할 수 없습니다.`)) return;
      section.dataset.photoBusy = "true";
      const button = form.querySelector("button[type=submit]");
      button.disabled = true;
      status.textContent = "사진을 삭제하는 중입니다…";
      try {
        const data = new FormData(form);
        data.set("confirm_delete", "yes");
        const response = await fetch(form.action, {
          method: "POST", credentials: "same-origin", body: data,
          headers: { "X-Requested-With": "XMLHttpRequest" },
        });
        if (response.redirected) throw new Error("로그인 상태를 확인한 뒤 다시 시도해 주세요.");
        const result = await response.json().catch(() => ({}));
        if (!response.ok || result.ok !== true) throw new Error(result.error || "사진을 삭제하지 못했습니다. 다시 시도해 주세요.");
        if (section.hasAttribute("data-photo-album")) {
          window.location.reload();
          return;
        }
        form.closest("figure").remove();
        const count = section.querySelector("[data-photo-count]");
        if (count) count.textContent = `${result.count} / 10`;
        const upload = section.querySelector("[data-photo-upload-form]");
        if (upload && result.count < 10) {
          upload.querySelector("[data-photo-input]").disabled = false;
          upload.querySelector("button[type=submit]").disabled = false;
          upload.querySelector("[data-photo-status]").textContent = "";
        }
        section.refreshPhotoUploader?.();
        status.textContent = "사진을 삭제했습니다.";
      } catch (error) {
        status.textContent = error.message;
      } finally {
        delete section.dataset.photoBusy;
        button.disabled = false;
      }
    });
  }

  const crcTable = (() => {
    const table = new Uint32Array(256);
    for (let n = 0; n < 256; n += 1) {
      let c = n;
      for (let k = 0; k < 8; k += 1) c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
      table[n] = c >>> 0;
    }
    return table;
  })();

  function crc32(bytes) {
    let crc = 0xffffffff;
    for (const byte of bytes) crc = crcTable[(crc ^ byte) & 0xff] ^ (crc >>> 8);
    return (crc ^ 0xffffffff) >>> 0;
  }

  function concatBytes(parts) {
    const result = new Uint8Array(parts.reduce((sum, part) => sum + part.length, 0));
    let offset = 0;
    for (const part of parts) { result.set(part, offset); offset += part.length; }
    return result;
  }

  function zipDateTime(now) {
    return {
      time: (now.getHours() << 11) | (now.getMinutes() << 5) | Math.floor(now.getSeconds() / 2),
      date: ((Math.max(1980, now.getFullYear()) - 1980) << 9) | ((now.getMonth() + 1) << 5) | now.getDate(),
    };
  }

  function makeZip(files) {
    const encoder = new TextEncoder();
    const locals = [];
    const centrals = [];
    const now = zipDateTime(new Date());
    let offset = 0;
    for (const file of files) {
      const name = encoder.encode(file.name.replace(/\\/g, "_").replace(/\.\./g, "_"));
      const data = file.data;
      const crc = crc32(data);
      const local = new Uint8Array(30);
      const lv = new DataView(local.buffer);
      lv.setUint32(0, 0x04034b50, true); lv.setUint16(4, 20, true); lv.setUint16(6, 0x0800, true); lv.setUint16(8, 0, true);
      lv.setUint16(10, now.time, true); lv.setUint16(12, now.date, true); lv.setUint32(14, crc, true); lv.setUint32(18, data.length, true); lv.setUint32(22, data.length, true); lv.setUint16(26, name.length, true);
      locals.push(local, name, data);
      const central = new Uint8Array(46);
      const cv = new DataView(central.buffer);
      cv.setUint32(0, 0x02014b50, true); cv.setUint16(4, 20, true); cv.setUint16(6, 20, true); cv.setUint16(8, 0x0800, true); cv.setUint16(10, 0, true);
      cv.setUint16(12, now.time, true); cv.setUint16(14, now.date, true); cv.setUint32(16, crc, true); cv.setUint32(20, data.length, true); cv.setUint32(24, data.length, true); cv.setUint16(28, name.length, true); cv.setUint32(42, offset, true);
      centrals.push(central, name);
      offset += local.length + name.length + data.length;
    }
    const centralBytes = concatBytes(centrals);
    const end = new Uint8Array(22);
    const ev = new DataView(end.buffer);
    ev.setUint32(0, 0x06054b50, true); ev.setUint16(8, files.length, true); ev.setUint16(10, files.length, true); ev.setUint32(12, centralBytes.length, true); ev.setUint32(16, offset, true);
    return new Blob([...locals, centralBytes, end], { type: "application/zip" });
  }

  function initializeAlbum(album) {
    if (album.dataset.downloadInitialized) return;
    album.dataset.downloadInitialized = "true";
    const button = album.querySelector("[data-download-photos]");
    if (!button) return;
    button.addEventListener("click", async () => {
      const status = album.querySelector("[data-zip-status]");
      button.disabled = true;
      const token = album.querySelector("[data-photo-confirm-token]");
      const controls = album.querySelectorAll("[data-photo-confirm-control]");
      if (token) token.value = "";
      controls.forEach((control) => { control.disabled = true; if (control.type === "checkbox") control.checked = false; });
      try {
        const manifestResponse = await fetch(album.dataset.manifestUrl, { credentials: "same-origin" });
        if (!manifestResponse.ok || manifestResponse.redirected) throw new Error("사진 목록을 불러오지 못했습니다. 로그인 상태를 확인해 주세요.");
        const manifest = await manifestResponse.json();
        if (!manifest.files.length) throw new Error("새로 보관할 사진이 없습니다.");
        const files = [];
        for (let index = 0; index < manifest.files.length; index += 1) {
          status.textContent = `전체 ${manifest.files.length}장 중 ${index + 1}번째 사진을 담는 중입니다…`;
          const response = await fetch(manifest.files[index].url, { credentials: "same-origin" });
          if (!response.ok || response.redirected) throw new Error(`${index + 1}번째 사진을 불러오지 못했습니다. 로그인 상태를 확인해 주세요.`);
          files.push({ name: manifest.files[index].name, data: new Uint8Array(await response.arrayBuffer()) });
        }
        status.textContent = "ZIP 파일을 만드는 중입니다…";
        const blob = makeZip(files);
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        const scope = new URL(album.dataset.manifestUrl, window.location.href).searchParams.get("scope") || "full";
        link.download = `to-do-list-photos-${scope}-${new Date().toISOString().slice(0, 10)}.zip`;
        link.click();
        setTimeout(() => URL.revokeObjectURL(link.href), 1000);
        if (token && manifest.confirmation_token) {
          token.value = manifest.confirmation_token;
          controls.forEach((control) => { control.disabled = false; });
        }
        status.textContent = `사진 ${files.length}장의 ZIP 다운로드를 요청했어요. 실제 저장된 파일을 확인해 주세요.`;
      } catch (error) {
        status.textContent = error.message;
      } finally {
        button.disabled = false;
      }
    });
  }

  function initializePhotoSections(root = document) {
    root.querySelectorAll("[data-photo-delete-form]").forEach(initializePhotoDeletion);
    root.querySelectorAll("[data-photo-section]").forEach(initializeUploader);
    root.querySelectorAll("[data-photo-album]").forEach(initializeAlbum);
  }

  window.initializePhotoSections = initializePhotoSections;
  window.canLeavePhotoSelection = () => {
    const sections = Array.from(document.querySelectorAll("[data-photo-section]"));
    if (sections.some(section => section.dataset.photoBusy)) {
      window.alert("사진을 처리하고 있어요. 끝난 뒤 다른 날짜로 이동해 주세요."); return false;
    }
    return !sections.some(section => section.dataset.photoPending) || window.confirm("아직 올리지 않은 사진이 있어요. 선택을 취소하고 다른 날짜로 이동할까요?");
  };
  window.preservePhotoSelection = (current, replacement) => {
    const existing = current.querySelector("[data-photo-section]");
    const next = replacement.querySelector("[data-photo-section]");
    if (existing && next && (existing.dataset.photoPending || existing.dataset.photoBusy) && existing.dataset.uploadUrl === next.dataset.uploadUrl) next.replaceWith(existing);
  };
  window.addEventListener?.("beforeunload", event => {
    if (Array.from(document.querySelectorAll("[data-photo-section]")).some(section => section.dataset.photoPending || section.dataset.photoBusy)) { event.preventDefault(); event.returnValue = ""; }
  });
  initializePhotoSections();
})();
