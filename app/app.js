const state = {
  projects: [],
  current: null,
  format: "reel",
  mode: "template",
  file: null,
};

const $ = (id) => document.getElementById(id);

async function api(path, body) {
  const opts = body
    ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : {};
  const res = await fetch(path, opts);
  return res.json();
}

function shapeOf(p) {
  if (!p.w || !p.h) return "";
  const r = p.w / p.h;
  if (Math.abs(r - 16 / 9) < 0.02) return "16:9";
  if (Math.abs(r - 9 / 16) < 0.02) return "9:16";
  if (Math.abs(r - 1) < 0.02) return "1:1";
  if (Math.abs(r - 4 / 5) < 0.02) return "4:5";
  return `${p.w}x${p.h}`;
}

/* ------------------------------------------------------------------ list */
function drawList() {
  const list = $("list");
  list.innerHTML = "";
  if (!state.projects.length) {
    list.innerHTML = '<li class="empty-li">Nothing here yet. Start one below.</li>';
    return;
  }
  for (const p of state.projects) {
    const li = document.createElement("li");
    if (state.current && state.current.slug === p.slug) li.className = "on";
    const made = p.video ? '<span class="done"> - made</span>' : "";
    li.innerHTML = `<span class="n">${escapeHtml(p.name)}</span>
      <span class="s">${shapeOf(p)} ${p.w} x ${p.h}${made}</span>`;
    li.onclick = () => { location.hash = p.slug; select(p.slug); };
    list.appendChild(li);
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

/* ---------------------------------------------------------------- shapes */
function drawFormats() {
  const box = $("formats");
  box.innerHTML = "";
  const items = [
    ["reel", "Reel", "9:16 phone"],
    ["portrait", "Portrait", "4:5 feed"],
    ["square", "Square", "1:1 grid"],
    ["wide", "YouTube", "16:9 wide"],
  ];
  for (const [key, name, note] of items) {
    const b = document.createElement("button");
    b.className = "fmt" + (state.format === key ? " on" : "");
    b.innerHTML = `<b>${name}</b><span>${note}</span>`;
    b.onclick = () => { state.format = key; drawFormats(); };
    box.appendChild(b);
  }
}

/* ------------------------------------------------------------------ modes */
function setMode(mode) {
  state.mode = mode;
  $("tab-template").classList.toggle("on", mode === "template");
  $("tab-record").classList.toggle("on", mode === "recording");
  $("record-box").hidden = mode !== "recording";
  $("create").textContent = mode === "recording" ? "Create and cut" : "Create";
  $("newmsg").textContent = "";
}

/* ---------------------------------------------------------------- project */
function select(slug) {
  const p = state.projects.find((x) => x.slug === slug);
  if (!p) return;
  state.current = p;
  drawList();
  $("empty").hidden = true;
  $("work").hidden = false;
  $("title").textContent = p.name;
  $("sub").textContent = `${p.w} x ${p.h} - ${p.fps} frames a second`;
  $("log").textContent = "Ready.";
  $("links").innerHTML = "";

  if (p.video) {
    const poster = p.poster ? ` poster="${p.poster}"` : "";
    $("stage").innerHTML = `<video controls playsinline preload="metadata"${poster} src="${p.video}"></video>`;
  } else {
    $("stage").innerHTML = '<div class="wait">No video yet. Press "Make it".</div>';
  }

  if (p.review) {
    const a = document.createElement("a");
    a.href = p.review;
    a.target = "_blank";
    a.textContent = "Open the review page";
    $("links").appendChild(a);
  }
}

async function refresh() {
  const data = await api("/api/projects");
  state.projects = data.projects;
  setResolve(data.resolve);
  drawList();
  if (state.current) {
    const fresh = state.projects.find((x) => x.slug === state.current.slug);
    if (fresh) select(fresh.slug);
  }
}

function setResolve(open) {
  const pill = $("resolve");
  if (open) {
    pill.textContent = "Resolve is open";
    pill.className = "pill ok";
  } else {
    pill.textContent = "Resolve is closed - open it first";
    pill.className = "pill bad";
  }
}

/* ------------------------------------------------------------------- jobs */
async function watch(job, button, doneLabel) {
  const timer = setInterval(async () => {
    const data = await api("/api/job/" + job);
    $("log").textContent = data.log.join("\n");
    $("log").scrollTop = $("log").scrollHeight;

    if (!data.running) {
      clearInterval(timer);
      button.disabled = false;
      button.textContent = doneLabel;
      if (data.code === 0) {
        const kept = $("log").textContent;
        await refresh();
        $("log").textContent = kept + "\n\nDone. The video is below.";
      } else {
        $("log").textContent += "\n\nStopped with a problem. Read the lines above.";
      }
    }
  }, 900);
}

async function build() {
  if (!state.current) return;
  const button = $("build");
  button.disabled = true;
  button.textContent = "Working...";
  $("log").textContent = "Starting...";

  const { job, error } = await api("/api/build", { slug: state.current.slug });
  if (error || !job) {
    $("log").textContent = error || "Could not start.";
    button.disabled = false;
    button.textContent = "Make it";
    return;
  }
  watch(job, button, "Make it again");
}

/* ----------------------------------------------------------------- create */
async function create() {
  const name = $("name").value.trim();
  const msg = $("newmsg");
  msg.textContent = "";
  if (!name) { msg.textContent = "Type a name first."; return; }

  if (state.mode === "recording") {
    return createRecording(name);
  }

  const res = await api("/api/new", { name, format: state.format });
  if (res.error) { msg.textContent = res.error; return; }

  $("name").value = "";
  await refresh();
  location.hash = res.slug;
  select(res.slug);
}

async function createRecording(name) {
  const msg = $("newmsg");
  if (!state.file) { msg.textContent = "Choose a video first."; return; }

  const button = $("create");
  button.disabled = true;
  button.textContent = "Uploading...";
  msg.textContent = "";

  const q = new URLSearchParams({
    name,
    format: state.format,
    pause: $("pause").value,
  });

  let res;
  try {
    const r = await fetch("/api/from-recording?" + q.toString(), {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: state.file,
    });
    res = await r.json();
  } catch (err) {
    res = { error: "The upload did not finish. Try a smaller file." };
  }

  button.disabled = false;
  button.textContent = "Create and cut";

  if (res.error) { msg.textContent = res.error; return; }

  $("name").value = "";
  state.file = null;
  $("filename").textContent = "Choose a video";
  $("drop").classList.remove("has");
  await refresh();
  location.hash = res.slug;
  select(res.slug);
  $("log").textContent = "Cutting your recording...";
  watch(res.job, $("build"), "Make it again");
  $("build").disabled = true;
}

/* ------------------------------------------------------------------ wiring */
$("build").onclick = build;
$("create").onclick = create;
$("tab-template").onclick = () => setMode("template");
$("tab-record").onclick = () => setMode("recording");
$("name").addEventListener("keydown", (e) => { if (e.key === "Enter") create(); });

$("drop").onclick = () => $("file").click();
$("file").onchange = () => {
  const f = $("file").files[0];
  if (!f) return;
  state.file = f;
  $("filename").textContent = f.name;
  $("drop").classList.add("has");
};

drawFormats();
setMode(new URLSearchParams(location.search).get("tab") === "record" ? "recording" : "template");
refresh().then(() => {
  const want = location.hash.replace("#", "");
  if (want) select(want);
});

window.addEventListener("hashchange", () => {
  const want = location.hash.replace("#", "");
  if (want) select(want);
});

setInterval(() => {
  api("/api/projects").then((d) => setResolve(d.resolve)).catch(() => {});
}, 8000);
