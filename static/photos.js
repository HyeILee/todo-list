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

  function initializeUploader(section) {
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
  initializePhotoSections();
})();
