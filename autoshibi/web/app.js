/* AutoShibi Studio 2 — interface (vanilla JS, aucun CDN : fonctionne hors-ligne) */
"use strict";

const $ = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => [...r.querySelectorAll(s)];

const PRESETS = {
  equilibre:{derush:true,derush_level:3,max_pause_s:0.6,max_shot_s:7.0,zoom:true,zoom_coverage:0.80,zoom_intensity:1.0,zoom_out_ratio:0.45,shibi:true},
  rythme:{derush:true,derush_level:3,max_pause_s:0.4,max_shot_s:5.0,zoom:true,zoom_coverage:0.95,zoom_intensity:1.3,zoom_out_ratio:0.50,shibi:true,max_hold:2.6},
  doux:{derush:true,derush_level:1,max_pause_s:1.0,max_shot_s:10.0,zoom:true,zoom_coverage:0.55,zoom_intensity:0.6,zoom_out_ratio:0.40,shibi:true,linger_s:0.3},
  zooms:{zoom:true,shibi:false},
  shibis:{zoom:false,shibi:true},
};
const LEVEL_HINTS = {
  1:"Léger : ne retire que les erreurs, le hors-sujet et les longs blancs (> 4 s).",
  2:"Normal : retire aussi les hésitations longues et les temps morts (> 2,5 s).",
  3:"Serré : très rythmé, environ 1/4 de la vidéo supprimée (temps morts dès 1,5 s, passages sans intérêt).",
};

const S = {
  boot:null, job:{}, projects:[], projectInfo:null, videoGen:"", videoEdit:"",
  tracks:null, running:false, finished:false, es:null,
  stages:{}, stageOrder:[], tasks:new Map(), lastEvent:0, elapsedBase:0, elapsedAt:0,
  summary:null, galleryLoaded:false, poseTimer:null, checkOnly:false,
};

/* ---------------- utilitaires ---------------- */
async function api(path, method="GET", body=null){
  const opt = {method, headers:{"Content-Type":"application/json"}};
  if(body !== null) opt.body = JSON.stringify(body);
  const r = await fetch(path, opt);
  return r.json();
}
const debounce = (fn, ms) => { let t; return (...a)=>{ clearTimeout(t); t=setTimeout(()=>fn(...a), ms); }; };
function toast(msg, kind=""){
  const el = document.createElement("div");
  el.className = "toast " + kind; el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(()=>{ el.style.opacity="0"; el.style.transition="opacity .4s"; setTimeout(()=>el.remove(), 450); }, 4200);
}
function fmtDur(s){
  if(s==null || isNaN(s)) return "~";
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s/3600), m = Math.floor((s%3600)/60), ss = s%60;
  if(h) return `${h}h ${String(m).padStart(2,"0")}m`;
  return `${m}:${String(ss).padStart(2,"0")}`;
}
function fmtDate(ts){
  if(!ts) return "";
  return new Date(ts*1000).toLocaleDateString("fr-FR",{day:"numeric",month:"short"}) +
         " " + new Date(ts*1000).toLocaleTimeString("fr-FR",{hour:"2-digit",minute:"2-digit"});
}
function fmtSize(b){
  if(b==null) return "";
  if(b > 1e9) return (b/1e9).toFixed(1)+" Go";
  if(b > 1e6) return (b/1e6).toFixed(0)+" Mo";
  return Math.round(b/1e3)+" Ko";
}
const baseName = p => (p||"").split(/[\\/]/).pop();
const pct = (x, d=0) => (x*100).toFixed(d).replace(".", ",") + " %";
const num = (x, d=2) => (+x).toFixed(d).replace(".", ",");

/* ---------------- réglages : lecture / écriture des contrôles ---------------- */
function syncVideoField(){
  S.job.video = S.job.mode === "edit" ? S.videoEdit : S.videoGen;
}
function sliderFill(el){
  const pc = (el.value - el.min) / (el.max - el.min) * 100;
  el.style.setProperty("--fill", pc + "%");
}
function setOut(id, txt){ const el=$(id); if(el) el.textContent = txt; }

function renderControls(){
  const j = S.job;
  // modes & panneaux
  $("#modeGenerate").classList.toggle("active", j.mode==="generate");
  $("#modeEdit").classList.toggle("active", j.mode==="edit");
  $("#modeGenerate").setAttribute("aria-checked", j.mode==="generate");
  $("#modeEdit").setAttribute("aria-checked", j.mode==="edit");
  $("#srcGenerate").hidden = j.mode!=="generate";
  $("#srcEdit").hidden = j.mode!=="edit";
  $("#cardDerush").hidden = j.mode==="edit";
  $("#rowKeepZoom").hidden = j.mode!=="edit";
  $("#rowReplace").hidden = j.mode!=="edit";

  $("#inVideo").value = S.videoGen || "";
  $("#inVideoEdit").value = S.videoEdit || "";
  $("#swCopy").checked = !!j.work_on_copy;
  $("#copyHint").textContent = j.work_on_copy
    ? "Ton projet d’origine n’est pas touché : le résultat est un nouveau projet « -AutoShibi- »."
    : "Décoché : le projet est modifié directement — une sauvegarde de son état actuel est faite juste avant.";
  if($("#selTemplate").value !== j.template) $("#selTemplate").value = j.template;
  if($("#selTemplateEdit").value !== j.template) $("#selTemplateEdit").value = j.template;

  // dérush
  $("#swDerush").checked = !!j.derush;
  $("#cardDerush").classList.toggle("off", !j.derush);
  $$("#segLevel button").forEach(b=>b.classList.toggle("active", +b.dataset.level === +j.derush_level));
  $("#levelHint").textContent = LEVEL_HINTS[j.derush_level] || "";
  setRange("#rgMaxPause", j.max_pause_s, v=>num(v)+" s", "#outMaxPause");
  setRange("#rgMaxShot", j.max_shot_s, v=>num(v,1)+" s", "#outMaxShot");
  // zooms
  $("#swZoom").checked = !!j.zoom;
  $("#bodyZoom").closest(".card").classList.toggle("off", !j.zoom);
  setRange("#rgZoomCoverage", j.zoom_coverage, v=>pct(v)+" des plans", "#outZoomCoverage");
  setRange("#rgZoomIntensity", j.zoom_intensity, v=> v>0 ? `100 → ${Math.round(100+10*v)} %` : "aucun", "#outZoomIntensity");
  setRange("#rgZoomOut", j.zoom_out_ratio, v=>pct(v), "#outZoomOut");
  $("#chkKeepZoom").checked = !!j.keep_existing_zoom;
  // shibis
  $("#swShibi").checked = !!j.shibi;
  $("#bodyShibi").closest(".card").classList.toggle("off", !j.shibi);
  $$("#segSource button").forEach(b=>b.classList.toggle("active", b.dataset.source===j.shibi_source));
  $("#tracksPanel").hidden = j.shibi_source!=="tracks";
  fillTrackSelect("#selTrackOmg", j.track_agentomg);
  fillTrackSelect("#selTrackMarv", j.track_marvouch);
  setRange("#rgVad", j.vad_rel_db, v=>Math.round(v)+" dB", "#outVad");
  setRange("#rgShibiScale", j.shibi_scale, v=>pct(v), "#outShibiScale");
  setRange("#rgLinger", j.linger_s, v=>num(v)+" s", "#outLinger");
  setRange("#rgMaxHold", j.max_hold, v=>num(v,1)+" s", "#outMaxHold");
  $("#chkSwap").checked = !!j.swap_sides;
  $("#chkReplace").checked = !!j.replace_old_shibis;
  $("#chkRepick").checked = !!j.repick;
  // avancé
  $("#inProjectsDir").value = j.projects_dir || "";
  setRange("#rgWorkers", j.workers, v=>Math.round(v), "#outWorkers");
  $("#chkClose").checked = !!j.close_capcut;
  $("#chkOpen").checked = !!j.open_capcut;
  $("#chkFresh").checked = !!j.fresh;
  // presets
  $$("#presetRow .preset").forEach(b=>b.classList.toggle("active", b.dataset.preset===j.preset));
  document.documentElement.style.setProperty("--shibi-scale", j.shibi_scale);
  updateStagePreview(false);
  updateSummary();
}
function setRange(sel, val, fmt, outSel){
  const el = $(sel); el.value = val; sliderFill(el); setOut(outSel, fmt(+val));
}
function fillTrackSelect(sel, val){
  const el = $(sel);
  const streams = (S.tracks && S.tracks.streams) || [];
  const levels = (S.tracks && S.tracks.levels) || [];
  const items = [];
  const nMax = Math.max(8, streams.length);
  for(let n=1;n<=nMax;n++){
    const st = streams[n-1], lv = levels[n-1];
    let label = "Piste " + n;
    if(st && st.title) label += " — " + st.title;
    if(lv) label += ` (voix ${Math.round(lv.voice*100)} %)`;
    items.push(`<option value="${n}">${label}</option>`);
  }
  if(el.dataset.built !== String(streams.length)+"/"+String(levels.length)){
    el.innerHTML = items.join("");
    el.dataset.built = String(streams.length)+"/"+String(levels.length);
  }
  el.value = String(val);
}

/* sauvegarde + vérifications automatiques */
const scheduleSave = debounce(async ()=>{
  syncVideoField();
  const key = $("#inApiKey").value.trim();
  const r = await api("/api/settings", "POST", {job:S.job, api_key:key});
  if(key){ $("#inApiKey").value=""; refreshKeyPill(r.api_key_present, "••••"+key.slice(-4)); }
  const h = $("#savedHint"); h.textContent = "Réglages enregistrés ✓"; h.classList.add("show");
  setTimeout(()=>h.classList.remove("show"), 1600);
}, 550);
const schedulePreflight = debounce(()=>{ refreshPreflight(); refreshEstimate(); }, 650);
function changed(recipe=false){
  if(recipe && S.job.preset !== "perso"){
    S.job.preset = "perso";
    $$("#presetRow .preset").forEach(b=>b.classList.remove("active"));
  }
  updateSummary();
  scheduleSave(); schedulePreflight();
}

/* ---------------- résumé / vérifications ---------------- */
function updateSummary(){
  const j = S.job, ul = $("#sumLines");
  const lines = [];
  if(j.mode==="generate"){
    lines.push(`<li>Nouveau montage depuis <b>${baseName(S.videoGen) || "video.mkv"}</b></li>`);
    lines.push(`<li>Projet créé en clonant <b>${j.template || "AutoShibi-V4"}</b></li>`);
    lines.push(j.derush ? `<li>Dérush niveau <b>${j.derush_level}/3</b>, pauses ≤ ${num(j.max_pause_s)} s</li>`
                        : "<li>Sans dérush : toute la vidéo est gardée</li>");
  } else {
    lines.push(`<li>Projet <b>${j.project || "—"}</b> ${j.work_on_copy ? "copié puis modifié" : "modifié directement (sauvegarde avant)"}</li>`);
    if(S.projectInfo) lines.push(`<li>${S.projectInfo.clips} plans · ${fmtDur(S.projectInfo.duration_s)} de timeline</li>`);
  }
  lines.push(j.zoom ? `<li>Zooms sur <b>${pct(j.zoom_coverage)}</b> des plans, intensité ${num(j.zoom_intensity,1)}</li>` : "<li>Pas de zooms ajoutés</li>");
  lines.push(j.shibi ? `<li>Shibis (${j.shibi_source==="tracks" ? "pistes séparées, exact" : "devinés par l’IA"}), taille ${pct(j.shibi_scale)}</li>` : "<li>Pas de shibis</li>");
  ul.innerHTML = lines.join("");
}
async function refreshPreflight(){
  syncVideoField();
  const list = $("#preflightList");
  try{
    const r = await api("/api/preflight", "POST", {job:S.job});
    list.innerHTML = "";
    let fail = false;
    (r.items||[]).forEach(it=>{
      if(it.status==="fail") fail = true;
      const li = document.createElement("li");
      const ico = it.status==="ok" ? "✓" : it.status==="warn" ? "!" : "✗";
      li.innerHTML = `<span class="pf-ico pf-${it.status}">${ico}</span><span>${escapeHtml(it.label)}${it.hint?`<span class="pf-hint">${escapeHtml(it.hint)}</span>`:""}</span>`;
      list.appendChild(li);
    });
    $("#btnRun").disabled = fail;
    $("#btnRun").title = fail ? "Corrige d’abord les points en rouge" : "";
  }catch(e){ /* serveur pas prêt */ }
}
async function refreshEstimate(){
  syncVideoField();
  try{
    const r = await api("/api/estimate", "POST", {job:S.job});
    const box = $("#estimateBox");
    if(r.duration_s){
      let txt = `🎞️ ${fmtDur(r.duration_s)} de vidéo ≈ <b>${r.chunks} tranche(s)</b> de 5 min pour Gemini.`;
      const cached = (r.derush_cached||0) + (r.speech_cached||0);
      if(cached) txt += ` <b>${cached}</b> tranche(s) déjà en cache : elles ne seront pas refaites.`;
      else txt += " Premier passage sur cette vidéo : tout sera analysé (puis mis en cache).";
      box.innerHTML = txt;
    } else box.innerHTML = "";
  }catch(e){}
}
function escapeHtml(s){
  return String(s).replace(/[&<>"']/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

/* ---------------- projets & modèles ---------------- */
function renderProjects(filter=""){
  const box = $("#projectList");
  const f = filter.trim().toLowerCase();
  const shown = S.projects.filter(p=>!f || p.name.toLowerCase().includes(f));
  if(!S.projects.length){
    box.innerHTML = `<div class="empty-list">Aucun projet CapCut trouvé.<br><small>Vérifie le dossier des projets (Réglages avancés) et que CapCut a bien enregistré tes projets.</small></div>`;
    return;
  }
  box.innerHTML = shown.map(p=>
    `<button type="button" class="proj-item ${p.name===S.job.project?"active":""}" data-name="${escapeHtml(p.name)}" role="option">
       <span class="p-name">${escapeHtml(p.name)}</span><span class="p-date">${fmtDate(p.modified)}</span>
     </button>`).join("");
  $$(".proj-item", box).forEach(b=>b.addEventListener("click", ()=>selectProject(b.dataset.name)));
}
async function selectProject(name){
  S.job.project = name;
  renderProjects($("#projSearch").value);
  const info = $("#projectInfo");
  info.className = "proj-info"; info.innerHTML = "Lecture du projet…";
  const r = await api("/api/project-info?name=" + encodeURIComponent(name) + "&dir=" + encodeURIComponent(S.job.projects_dir||""));
  if(r.ok){
    S.projectInfo = r.info;
    const bits = [`✓ ${r.info.clips} plans`, fmtDur(r.info.duration_s), "vidéo : " + (baseName(r.info.video_path)||"?")];
    if(r.info.with_scale_keyframes) bits.push(`${r.info.with_scale_keyframes} déjà zoomés`);
    if(r.info.previous_shibi_tracks) bits.push("shibis déjà posés");
    info.textContent = bits.join(" · ") + (r.info.has_keyframe_sample ? "" : " — aucun keyframe d’échelle : choisis un modèle qui en contient un.");
    info.classList.toggle("warn", !r.info.has_keyframe_sample);
  } else {
    S.projectInfo = null;
    info.className = "proj-info warn"; info.textContent = "⚠ " + r.error;
  }
  updateSummary(); scheduleSave(); schedulePreflight();
}
async function refreshProjects(){
  const r = await api("/api/projects?dir=" + encodeURIComponent(S.job.projects_dir||""));
  S.projects = r.projects || [];
  $("#projectsDirHint").textContent = r.projects_dir ? "Détecté : " + r.projects_dir : "Introuvable : indique le dossier com.lveditor.draft.";
  const opts = S.projects.map(p=>p.name);
  [$("#selTemplate"), $("#selTemplateEdit")].forEach(sel=>{
    const cur = S.job.template;
    sel.innerHTML = opts.map(n=>`<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join("");
    if(cur && !opts.includes(cur)) sel.innerHTML += `<option value="${escapeHtml(cur)}">${escapeHtml(cur)} (introuvable ?)</option>`;
    sel.value = cur || "";
  });
  if(S.job.mode==="edit" && S.projects.length && !S.projects.some(p=>p.name===S.job.project))
    S.job.project = S.projects[0].name;
  renderProjects($("#projSearch").value);
  if(S.job.project) selectProject(S.job.project);
}

/* ---------------- vidéo & pistes ---------------- */
async function showVideo(path){
  $("#videoName").textContent = baseName(path) || "Aucune vidéo choisie";
  if(!path){ $("#videoMeta").textContent = "Le fichier de ton enregistrement (.mkv, .mp4…)."; return; }
  $("#videoMeta").textContent = "Lecture…";
  const r = await api("/api/video-info", "POST", {video:path});
  if(r.ok){
    const i = r.info;
    $("#videoMeta").textContent = `${i.width}x${i.height} · ${fmtDur(i.duration)} · ${i.audio_streams.length} piste(s) audio · ${fmtSize(i.size)}`;
  } else $("#videoMeta").textContent = "⚠ " + r.error;
}
async function detectTracks(){
  const video = S.job.mode==="edit" ? (S.videoEdit || (S.projectInfo && S.projectInfo.video_path)) : S.videoGen;
  if(!video){ toast("Choisis d’abord la vidéo (ou un projet dont la vidéo est détectée).", "err"); return; }
  const btn = $("#btnDetectTracks");
  btn.disabled = true; btn.textContent = "🔍 Analyse des pistes…";
  $("#trackLevels").innerHTML = `<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>`;
  try{
    const r = await api("/api/tracks", "POST", {video});
    if(!r.ok){ toast(r.error, "err"); $("#trackLevels").innerHTML=""; return; }
    S.tracks = r;
    fillTrackSelect("#selTrackOmg", S.job.track_agentomg);
    fillTrackSelect("#selTrackMarv", S.job.track_marvouch);
    $("#trackLevels").innerHTML = r.streams.map((st,i)=>{
      const lv = r.levels[i] || {};
      const voicePct = Math.round((lv.voice||0)*100);
      return `<div class="tl-row"><span>Piste ${st.n}${st.title?" — "+escapeHtml(st.title):""}</span>
        <span class="tl-bar"><span class="tl-fill voice" style="width:${voicePct}%"></span></span>
        <span>${lv.silent?"silencieuse":`voix ${voicePct} % · moy. ${lv.mean_db} dB`}</span></div>`;
    }).join("");
    // suggestion par le titre des pistes
    let hint = `${r.streams.length} piste(s) audio. `, guess = {};
    r.streams.forEach(st=>{
      const t = (st.title||"").toLowerCase();
      if(/omg|agent/.test(t)) guess.AgentOMG = st.n;
      if(/marv/.test(t)) guess.Marvouch = st.n;
    });
    if(guess.AgentOMG) S.job.track_agentomg = guess.AgentOMG;
    if(guess.Marvouch) S.job.track_marvouch = guess.Marvouch;
    if(guess.AgentOMG || guess.Marvouch){
      fillTrackSelect("#selTrackOmg", S.job.track_agentomg);
      fillTrackSelect("#selTrackMarv", S.job.track_marvouch);
      hint += "Attribution proposée d’après le nom des pistes — vérifie-la.";
    } else hint += "La piste avec le plus de voix est souvent le mix ; attribue les deux autres à chaque personne.";
    $("#tracksHint").textContent = hint;
    changed(true);
    toast("Pistes détectées : attribue celle de chaque personne.", "ok");
  }finally{
    btn.disabled = false; btn.textContent = "🔍 Détecter les pistes de la vidéo";
  }
}

/* ---------------- aperçu shibi & galerie ---------------- */
function poseSrc(who, n){ return `/api/asset?who=${who}&n=${n}`; }
function updateStagePreview(randomize=true){
  const omg = $("#stageOmg"), Marv = $("#stageMarv");
  const swap = S.job.swap_sides;
  const n1 = randomize ? 1+Math.floor(Math.random()*22) : 2;
  const n2 = randomize ? 1+Math.floor(Math.random()*22) : 12;
  if(randomize || !omg.src){ omg.src = poseSrc("AgentOMG", n1); Marv.src = poseSrc("Marvouch", n2); }
  const L = "2%", R = "2%";
  Marv.style.left = swap ? "auto" : L; Marv.style.right = swap ? R : "auto";
  Marv.style.transform = swap ? "" : "scaleX(-1)";   // Marvouch regarde vers le centre
  omg.style.left = swap ? L : "auto"; omg.style.right = swap ? "auto" : R;
  omg.style.transform = swap ? "scaleX(-1)" : "";
  const nm = $("#stageNameMarv"), no = $("#stageNameOmg");
  nm.style.left = swap ? "auto" : "10px"; nm.style.right = swap ? "10px" : "auto";
  no.style.left = swap ? "10px" : "auto"; no.style.right = swap ? "auto" : "10px";
  omg.onerror = ()=>{ omg.style.opacity = .0; };
  Marv.onerror = ()=>{ Marv.style.opacity = .0; };
  omg.onload = ()=>{ omg.style.opacity = 1; };
  Marv.onload = ()=>{ Marv.style.opacity = 1; };
}
async function loadGallery(){
  if(S.galleryLoaded) return; S.galleryLoaded = true;
  const fill = (id, who)=>{
    $(id).innerHTML = Array.from({length:22},(_,i)=>
      `<figure><img loading="lazy" src="${poseSrc(who, i+1)}" alt="pose ${i+1}" onerror="this.closest('figure').style.opacity=.25"><figcaption>${i+1}</figcaption></figure>`).join("");
  };
  fill("#galleryOmg", "AgentOMG"); fill("#galleryMarv", "Marvouch");
}

/* ---------------- explorateur de fichiers ---------------- */
let browseTarget = "videoGen";
async function openBrowse(path=""){
  const r = await api("/api/browse?path=" + encodeURIComponent(path));
  $("#browsePath").textContent = r.path;
  $("#btnBrowseUp").disabled = !r.parent;
  $("#btnBrowseUp").onclick = ()=>openBrowse(r.parent||"");
  $("#browseRoots").innerHTML = (r.roots||[]).map(rt=>
    `<button type="button" class="btn btn-small ${r.path.startsWith(rt.path)?"btn-secondary active":"btn-ghost"}" data-p="${escapeHtml(rt.path)}">${escapeHtml(rt.label)}</button>`).join("");
  $$("#browseRoots button").forEach(b=>b.onclick=()=>openBrowse(b.dataset.p));
  $("#browseList").innerHTML = (r.entries||[]).map(e=>
    `<button type="button" class="browse-item" data-p="${escapeHtml(e.path)}" data-dir="${e.dir?1:0}">
       <span>${e.dir?"📁":"🎬"}</span><span class="b-name">${escapeHtml(e.name)}</span>
       <span class="b-size">${e.dir?"":fmtSize(e.size)}</span></button>`).join("")
    || `<div class="empty-list">Aucun dossier ni vidéo ici.</div>`;
  $$("#browseList .browse-item").forEach(b=>b.onclick=()=>{
    if(b.dataset.dir==="1") openBrowse(b.dataset.p);
    else { chooseVideo(browseTarget, b.dataset.p); $("#browseModal").hidden = true; }
  });
}
function chooseVideo(which, path){
  if(which==="videoGen"){ S.videoGen = path; $("#inVideo").value = path; showVideo(path); }
  else { S.videoEdit = path; $("#inVideoEdit").value = path; }
  changed(false);
}
async function pickNative(kind){
  const r = await api("/api/pick", "POST", {kind});
  if(r.ok && r.path) return r.path;
  return null;
}

/* ---------------- exécution ---------------- */
function clientStages(job){
  const st = [["prep","Préparation"]];
  if(job.mode==="generate") st.push(["derush", job.derush ? "Dérush" : "Découpage des plans"]);
  if(job.shibi && job.shibi_source!=="off"){ st.push(["speech","Qui parle ?"], ["poses","Choix des poses"]); }
  st.push(["capcut","Projet CapCut"]);
  return st;
}
function renderStageSkeleton(stages){
  S.stageOrder = stages.map(s=>s.id||s[0]);
  S.stages = {};
  $("#stageList").innerHTML = stages.map(s=>{
    const id = s.id||s[0], title = s.title||s[1];
    S.stages[id] = {status:s.status||"pending", done:s.done||0, total:s.total||0, label:s.label||"", title};
    return `<li class="stage-item" id="st-${id}">
      <span class="st-ico">${S.stageOrder.indexOf(id)+1}</span>
      <div class="st-main"><div class="st-title">${title}<span class="st-count"></span></div>
      <div class="st-label"></div><div class="st-mini"><div></div></div></div></li>`;
  }).join("");
}
function paintStage(id){
  const st = S.stages[id]; const li = $("#st-"+id); if(!st || !li) return;
  li.classList.toggle("running", st.status==="running");
  li.classList.toggle("done", st.status==="done");
  li.querySelector(".st-ico").textContent = st.status==="done" ? "✓" : (S.stageOrder.indexOf(id)+1);
  li.querySelector(".st-count").textContent = st.total ? `${st.done}/${st.total}` : "";
  li.querySelector(".st-label").textContent = st.label || "";
  li.querySelector(".st-mini>div").style.width = st.total ? Math.min(100, st.done/st.total*100)+"%" : (st.status==="done" ? "100%" : "0%");
}
function addJournal(ev){
  const j = $("#journal");
  const div = document.createElement("div");
  div.className = "j-line " + (ev.level||"info");
  const t = new Date((ev.ts||Date.now()/1000)*1000).toLocaleTimeString("fr-FR",{hour:"2-digit",minute:"2-digit",second:"2-digit"});
  div.innerHTML = `<span class="j-time">${t}</span><span class="j-dot"></span><span>${escapeHtml(ev.msg)}</span>`;
  j.appendChild(div);
  while(j.children.length > 500) j.firstChild.remove();
  if($("#chkAutoscroll").checked) j.scrollTop = j.scrollHeight;
}
function paintTasks(){
  const ul = $("#liveTasks"); const arr = [...S.tasks.values()];
  $("#noTaskHint").hidden = arr.length>0;
  ul.innerHTML = arr.map(t=>{
    const secs = Math.max(0, Math.round((Date.now()-t.t0)/1000));
    return `<li><span class="task-dot"></span><span class="task-txt">${escapeHtml(t.label)}</span><span class="task-secs" data-t0="${t.t0}">${secs} s</span></li>`;
  }).join("");
}
function applyEvent(ev){
  S.lastEvent = Date.now();
  if(ev.elapsed!=null){ S.elapsedBase = ev.elapsed; S.elapsedAt = Date.now(); }
  if(ev.progress!=null){
    const p = Math.round(ev.progress*100);
    $("#bigBar").style.width = p + "%"; $("#bigPct").textContent = p + " %";
  }
  if(ev.type==="log") addJournal(ev);
  else if(ev.type==="stage"){
    const st = S.stages[ev.stage];
    if(st){ st.status = ev.status; st.done = ev.done??st.done; st.total = ev.total??st.total; st.label = ev.label??st.label; paintStage(ev.stage); }
    if(ev.eta!=null) $("#chipEta").textContent = "~" + fmtDur(ev.eta);
  }
  else if(ev.type==="task"){
    if(ev.status==="start") S.tasks.set(ev.task, {label:ev.label, t0:Date.now(), kind:ev.kind});
    if(ev.status==="update"){ const t=S.tasks.get(ev.task); if(t) t.label = ev.label; }
    if(ev.status==="end") S.tasks.delete(ev.task);
    paintTasks();
  }
  else if(ev.type==="heartbeat"){
    (ev.tasks||[]).forEach(t=>{
      const cur = S.tasks.get(t.id);
      if(cur){ cur.label = t.label; cur.t0 = Date.now() - t.seconds*1000; }
      else S.tasks.set(t.id, {label:t.label, t0:Date.now()-t.seconds*1000, kind:t.kind});
    });
    paintTasks();
    if(ev.eta!=null) $("#chipEta").textContent = "~" + fmtDur(ev.eta);
  }
  else if(ev.type==="done"){
    const replayed = ev.ts && (Date.now()/1000 - ev.ts) > 15;   // historique rejoué après réouverture
    finishUI(ev, replayed);
  }
}
function finishUI(ev, replayed=false){
  S.running = false; S.finished = true; S.summary = ev.summary||{};
  $("#btnCancel").hidden = true; $("#cancelHint").hidden = true;
  $("#btnBackSetup").hidden = false;
  $("#chipEtaWrap").hidden = true;
  if(ev.seconds!=null){ $("#chipElapsed").textContent = fmtDur(ev.seconds); S.elapsedBase = ev.seconds; }
  $("#runSubtitle").textContent = ev.status==="ok"
    ? "Tout est en cache : relancer avec d’autres réglages sera beaucoup plus rapide."
    : "Rien n’est perdu : le travail déjà fait est conservé en cache.";
  S.tasks.clear();
  if(ev.status==="ok"){
    $("#bigBar").style.width = "100%"; $("#bigPct").textContent = "100 %";
    const s = ev.summary||{};
    $("#donePanel").hidden = false; $("#errorPanel").hidden = true;
    if(s.checked){
      $("#runTitle").textContent = "Vérification terminée";
      $("#doneTitle").textContent = "Tout est prêt ✅";
      $("#doneText").textContent = "Ton installation et tes réglages sont bons. Tu peux lancer le montage.";
      $("#doneStats").innerHTML = "";
      $("#btnOpenProjectDone").hidden = true; $("#btnOpenCapcutDone").hidden = true;
    } else {
      $("#runTitle").textContent = "Montage terminé";
      $("#doneTitle").textContent = "Montage terminé 🎉";
      $("#doneText").innerHTML = `Le projet <b>${escapeHtml(s.project||"")}</b> est dans CapCut — ouvre-le et relis-le !${s.backup?`<br><small>Sauvegarde de l’état précédent : <span class="mono">${escapeHtml(s.backup)}</span></small>`:""}`;
      $("#btnOpenProjectDone").hidden = !s.project_dir;
      const stats = [];
      if(s.duration_before_s) stats.push([fmtDur(s.duration_after_s), "de vidéo montée (sur " + fmtDur(s.duration_before_s) + ")"]);
      if(s.plans) stats.push([s.plans, "plans"]);
      if(s.segments) stats.push([s.segments, "répliques"]);
      if(s.zooms!=null || s.dezooms!=null) stats.push([(s.zooms||0)+(s.dezooms||0), "zooms / dézooms animés"]);
      if(s.shibis && (s.shibis.AgentOMG||s.shibis.Marvouch)){
        stats.push([s.shibis.AgentOMG||0, "shibis AgentOMG"]); stats.push([s.shibis.Marvouch||0, "shibis Marvouch"]);
      }
      if(ev.seconds) stats.push([fmtDur(ev.seconds), "de traitement total"]);
      $("#doneStats").innerHTML = stats.map(([v,l])=>`<div class="stat"><b>${v}</b><span>${l}</span></div>`).join("");
      if(!replayed) toast("Montage terminé 🎉", "ok");
      refreshProjects();
    }
  } else {
    $("#donePanel").hidden = true; $("#errorPanel").hidden = false;
    const cancelled = ev.status==="cancelled";
    $("#runTitle").textContent = cancelled ? "Montage arrêté" : "Montage interrompu";
    $("#errorTitle").textContent = cancelled ? "🛑 Arrêté proprement" : "⚠️ Ça s’est arrêté";
    $("#errorText").textContent = ev.error || "Erreur inconnue.";
    $("#errorDetail").textContent = ev.detail || "(aucun détail)";
    if(!cancelled && !replayed) toast("Le montage s’est arrêté — voir le journal.", "err");
  }
  paintTasks();
}
function showRunView(checkOnly){
  S.checkOnly = checkOnly; S.running = true; S.finished = false;
  $("#viewSetup").hidden = true; $("#viewRun").hidden = false;
  $("#runTitle").textContent = checkOnly ? "Vérification en cours…" : "Montage en cours…";
  $("#donePanel").hidden = true; $("#errorPanel").hidden = true;
  $("#btnCancel").hidden = false; $("#btnCancel").disabled = false; $("#btnCancel").textContent = "■ Arrêter";
  $("#cancelHint").hidden = false; $("#btnBackSetup").hidden = true;
  $("#journal").innerHTML = ""; S.tasks.clear(); paintTasks();
  $("#bigBar").style.width = "0%"; $("#bigPct").textContent = "0 %"; $("#chipEta").textContent = "~";
  renderStageSkeleton(clientStages(S.job).map(([id,t])=>({id,title:t})));
  S.stageOrder.forEach(paintStage);
  S.lastEvent = Date.now();
  window.scrollTo({top:0, behavior:"smooth"});
  connectEvents();
}
function connectEvents(){
  if(S.es) S.es.close();
  S.es = new EventSource("/api/events");
  S.es.onmessage = e=>{ try{ applyEvent(JSON.parse(e.data)); }catch(_){} };
  S.es.onerror = ()=>{ /* le navigateur retente tout seul */ };
}
async function startJob(checkOnly){
  syncVideoField();
  const j = {...S.job};
  if(!checkOnly){
    if(j.mode==="edit" && !j.project){ toast("Choisis d’abord le projet CapCut à modifier.", "err"); return; }
    if(j.mode==="edit" && !j.zoom && !j.shibi){ toast("Active les zooms et/ou les shibis.", "err"); return; }
    if(j.shibi && j.shibi_source==="tracks" && +j.track_agentomg === +j.track_marvouch){
      toast("AgentOMG et Marvouch ne peuvent pas avoir la même piste.", "err"); return; }
    if(j.mode==="edit" && !j.work_on_copy &&
       !confirm(`« ${j.project} » va être modifié directement.\nUne sauvegarde de son état actuel est faite juste avant.\n\nContinuer ?`)) return;
    if(S.boot && S.boot.capcut_running && j.close_capcut &&
       !confirm("CapCut semble ouvert : le fermer maintenant ?\n(Sinon il écraserait les changements en se fermant.)")) { /* on lance quand même, sans forcer */ }
  }
  const key = $("#inApiKey").value.trim();
  const r = await api(checkOnly?"/api/check":"/api/run", "POST", {job:j, api_key:key});
  if(!r.ok){ toast(r.error||"Impossible de lancer.", "err"); return; }
  if(key) $("#inApiKey").value = "";
  showRunView(checkOnly);
}
/* chronomètres locaux + détecteur de silence */
setInterval(()=>{
  if($("#viewRun").hidden) return;
  const el = S.elapsedBase + (S.elapsedAt ? (Date.now()-S.elapsedAt)/1000 : 0);
  if(S.running) $("#chipElapsed").textContent = fmtDur(el);
  $$("#liveTasks .task-secs").forEach(s=>{
    s.textContent = Math.max(0, Math.round((Date.now()- +s.dataset.t0)/1000)) + " s";
  });
  const since = Math.round((Date.now()-S.lastEvent)/1000);
  const dot = $("#liveDot"), act = $("#chipActivity");
  if(S.running){
    if(since <= 4){ dot.classList.remove("stale"); act.textContent = "en direct"; }
    else { dot.classList.add("stale"); act.textContent = `… il y a ${since} s`; }
  } else if(S.finished) act.textContent = "terminé";
}, 1000);

/* ---------------- liaisons ---------------- */
function bindRange(sel, key, fmt, outSel, parse=parseFloat, recipe=true, extra=null){
  const el = $(sel);
  el.addEventListener("input", ()=>{
    S.job[key] = parse(el.value); sliderFill(el); setOut(outSel, fmt(parse(el.value)));
    if(extra) extra(parse(el.value));
    changed(recipe);
  });
}
function bindCheck(sel, key, recipe=true, extra=null){
  $(sel).addEventListener("change", e=>{
    S.job[key] = e.target.checked; if(extra) extra(); changed(recipe); renderControlsSoft();
  });
}
function renderControlsSoft(){ /* rafraîchit états dépendants sans perdre le focus */
  const j = S.job;
  $("#cardDerush").classList.toggle("off", !j.derush);
  $("#bodyZoom").closest(".card").classList.toggle("off", !j.zoom);
  $("#bodyShibi").closest(".card").classList.toggle("off", !j.shibi);
  updateSummary();
}
function bindAll(){
  $("#modeGenerate").onclick = ()=>{ if(S.job.mode!=="generate"){ S.job.mode="generate"; renderControls(); changed(false); } };
  $("#modeEdit").onclick = ()=>{ if(S.job.mode!=="edit"){ S.job.mode="edit"; renderControls(); changed(false);
    if(S.job.project && S.projects.some(p=>p.name===S.job.project)) selectProject(S.job.project);
    else if(S.projects.length) selectProject(S.projects[0].name); } };
  $$("#presetRow .preset").forEach(b=>b.onclick=()=>{
    Object.assign(S.job, PRESETS[b.dataset.preset]); S.job.preset = b.dataset.preset;
    renderControls(); changed(false);
    toast("Recette appliquée — tu peux encore ajuster ci-dessous.", "ok");
  });
  $$("#segLevel button").forEach(b=>b.onclick=()=>{ S.job.derush_level = +b.dataset.level; renderControls(); changed(true); });
  $$("#segSource button").forEach(b=>b.onclick=()=>{
    S.job.shibi_source = b.dataset.source;
    if(S.job.linger_s===0 || S.job.linger_s===0.35) S.job.linger_s = b.dataset.source==="tracks" ? 0 : 0.35;
    renderControls(); changed(true);
  });
  bindCheck("#swDerush","derush"); bindCheck("#swZoom","zoom"); bindCheck("#swShibi","shibi");
  bindCheck("#chkKeepZoom","keep_existing_zoom"); bindCheck("#chkReplace","replace_old_shibis");
  bindCheck("#chkRepick","repick"); bindCheck("#chkClose","close_capcut",false);
  bindCheck("#chkOpen","open_capcut",false); bindCheck("#chkFresh","fresh",false);
  bindCheck("#swCopy","work_on_copy",false, ()=>{ $("#copyHint").textContent = S.job.work_on_copy
    ? "Ton projet d’origine n’est pas touché : le résultat est un nouveau projet « -AutoShibi- »."
    : "Décoché : le projet est modifié directement — une sauvegarde de son état actuel est faite juste avant."; });
  bindCheck("#chkSwap","swap_sides",true, ()=>updateStagePreview(false));

  bindRange("#rgMaxPause","max_pause_s",v=>num(v)+" s","#outMaxPause");
  bindRange("#rgMaxShot","max_shot_s",v=>num(v,1)+" s","#outMaxShot");
  bindRange("#rgZoomCoverage","zoom_coverage",v=>pct(v)+" des plans","#outZoomCoverage");
  bindRange("#rgZoomIntensity","zoom_intensity",v=>v>0?`100 → ${Math.round(100+10*v)} %`:"aucun","#outZoomIntensity");
  bindRange("#rgZoomOut","zoom_out_ratio",v=>pct(v),"#outZoomOut");
  bindRange("#rgVad","vad_rel_db",v=>Math.round(v)+" dB","#outVad", v=>Math.round(parseFloat(v)));
  bindRange("#rgShibiScale","shibi_scale",v=>pct(v),"#outShibiScale", parseFloat, true,
    v=>document.documentElement.style.setProperty("--shibi-scale", v));
  bindRange("#rgLinger","linger_s",v=>num(v)+" s","#outLinger");
  bindRange("#rgMaxHold","max_hold",v=>num(v,1)+" s","#outMaxHold");
  bindRange("#rgWorkers","workers",v=>Math.round(v),"#outWorkers", v=>Math.round(parseFloat(v)), false);

  $("#selTrackOmg").onchange = e=>{ S.job.track_agentomg = +e.target.value; changed(true); };
  $("#selTrackMarv").onchange = e=>{ S.job.track_marvouch = +e.target.value; changed(true); };
  const tplChange = e=>{ S.job.template = e.target.value; renderControls(); changed(false); };
  $("#selTemplate").onchange = tplChange; $("#selTemplateEdit").onchange = tplChange;
  $("#inVideo").addEventListener("change", e=>{ S.videoGen = e.target.value.trim(); showVideo(S.videoGen); changed(false); });
  $("#inVideoEdit").addEventListener("change", e=>{ S.videoEdit = e.target.value.trim(); changed(false); });
  $("#inProjectsDir").addEventListener("change", e=>{ S.job.projects_dir = e.target.value.trim(); changed(false); refreshProjects(); });
  $("#inApiKey").addEventListener("input", ()=>scheduleSave());
  $("#btnShowKey").onclick = e=>{ const i=$("#inApiKey"); i.type = i.type==="password" ? "text" : "password"; e.target.textContent = i.type==="password"?"Afficher":"Masquer"; };
  $("#projSearch").addEventListener("input", e=>renderProjects(e.target.value));
  $("#btnRefreshProjects").onclick = refreshProjects;
  $("#btnRefreshTpl").onclick = refreshProjects;

  $("#dropVideo").addEventListener("click", e=>{ if(e.target.closest("button")) return; pickOrBrowse("videoGen"); });
  $("#dropVideo").addEventListener("keydown", e=>{ if(e.key==="Enter"||e.key===" "){ e.preventDefault(); pickOrBrowse("videoGen"); } });
  $("#btnPickVideo").onclick = ()=>pickOrBrowse("videoGen");
  $("#btnPickVideoEdit").onclick = ()=>pickOrBrowse("videoEdit");
  $("#btnBrowseVideo").onclick = ()=>{ browseTarget="videoGen"; $("#browseModal").hidden=false; openBrowse(S.videoGen ? S.videoGen.replace(/[\\/][^\\/]*$/,"") : ""); };
  $("#btnPickDir").onclick = async ()=>{
    const p = await pickNative("dir");
    if(p){ S.job.projects_dir = p; $("#inProjectsDir").value = p; changed(false); refreshProjects(); }
    else toast("Sélecteur natif indisponible ici : colle le chemin du dossier com.lveditor.draft.");
  };
  $("#btnDetectTracks").onclick = detectTracks;
  $("#galleryDetails").addEventListener("toggle", e=>{ if(e.target.open) loadGallery(); });

  $("#btnRun").onclick = ()=>startJob(false);
  $("#btnCheck").onclick = ()=>startJob(true);
  $("#btnCancel").onclick = async e=>{
    e.target.disabled = true; e.target.textContent = "Arrêt en cours…";
    await api("/api/cancel", "POST", {});
  };
  const back = ()=>{ $("#viewRun").hidden = true; $("#viewSetup").hidden = false; if(S.es) S.es.close(); refreshPreflight(); };
  $("#btnBackSetup").onclick = back; $("#btnRetry").onclick = back; $("#btnAgainDone").onclick = back;
  $("#btnCopyLog").onclick = ()=>{
    const txt = $$("#journal .j-line").map(l=>l.textContent).join("\n");
    navigator.clipboard && navigator.clipboard.writeText(txt);
    toast("Journal copié.", "ok");
  };
  $("#btnOpenCapcutDone").onclick = async ()=>{
    const r = await api("/api/open-capcut", "POST", {});
    toast(r.ok ? "CapCut se lance…" : "CapCut introuvable automatiquement : ouvre-le depuis ton bureau.", r.ok?"ok":"err");
  };
  $("#btnOpenProjectDone").onclick = ()=>api("/api/open-folder", "POST", {path:(S.summary||{}).project_dir||""});
  $("#btnOpenBase").onclick = ()=>api("/api/open-folder", "POST", {path:""});
  $("#btnClearCache").onclick = async ()=>{
    const video = S.job.mode==="edit" ? (S.videoEdit || (S.projectInfo && S.projectInfo.video_path)) : S.videoGen;
    if(!video){ toast("Choisis d’abord une vidéo.", "err"); return; }
    if(!confirm("Effacer les analyses déjà faites pour cette vidéo ?\nLe prochain montage redemandera tout à Gemini.")) return;
    const r = await api("/api/clear-cache", "POST", {video});
    toast(r.ok ? "Cache de la vidéo vidé." : (r.error||"Échec."), r.ok?"ok":"err");
    refreshEstimate();
  };

  $("#btnGuide").onclick = ()=>{ $("#helpModal").hidden = false; };
  $("#linkTemplateGuide").onclick = e=>{ e.preventDefault(); $("#helpModal").hidden = false; };
  $$("[data-close]").forEach(b=>b.onclick=()=>{ b.closest(".modal-back").hidden = true; });
  $$(".modal-back").forEach(m=>m.addEventListener("click", e=>{ if(e.target===m) m.hidden = true; }));
  document.addEventListener("keydown", e=>{ if(e.key==="Escape") $$(".modal-back").forEach(m=>m.hidden=true); });
}
async function pickOrBrowse(which){
  const p = await pickNative("video");
  if(p) chooseVideo(which, p);
  else { browseTarget = which; $("#browseModal").hidden = false; openBrowse(""); }
}
function refreshKeyPill(present, hint){
  const pill = $("#pillKey");
  pill.textContent = present ? `Clé Gemini ${hint||""}`.trim() : "Clé Gemini manquante";
  pill.className = "pill " + (present ? "ok" : "ko");
  $("#keyHint").textContent = present
    ? "Une clé est enregistrée (" + (hint||"") + "). Colle-en une autre pour la remplacer."
    : "Enregistrée dans .env sur ton PC uniquement, jamais envoyée ailleurs qu’à Google. Gratuite sur aistudio.google.com/apikey.";
}

/* ---------------- démarrage ---------------- */
(async function init(){
  bindAll();
  const boot = await api("/api/bootstrap");
  S.boot = boot; S.job = boot.job || {};
  if(boot.demo) $("#pillDemo").hidden = false;
  refreshKeyPill(boot.api_key_present, boot.api_key_hint);
  const pf = $("#pillFfmpeg");
  pf.textContent = boot.ffmpeg ? "ffmpeg ✓" : "ffmpeg manquant";
  pf.className = "pill " + (boot.ffmpeg ? "ok" : "ko");
  pf.title = boot.ffmpeg ? "" : "Installe-le : winget install ffmpeg, puis relance le logiciel";
  // répartition du champ vidéo unique selon le mode enregistré
  if(S.job.mode==="edit") S.videoEdit = S.job.video || ""; else S.videoGen = S.job.video || "";
  if(!S.videoGen && boot.default_video) S.videoGen = boot.default_video;
  S.projects = boot.projects || [];
  renderControls();
  await refreshProjects();
  if(S.videoGen) showVideo(S.videoGen);
  refreshPreflight(); refreshEstimate();
  // les poses de l'aperçu vivent
  S.poseTimer = setInterval(()=>{ if(!$("#viewSetup").hidden && !$("#bodyShibi").closest(".card").classList.contains("off")) updateStagePreview(true); }, 4500);
  // un montage était déjà en cours (onglet rouvert) ?
  const st = await api("/api/state");
  if(st.snapshot && (st.running || st.snapshot.finished)){
    showRunView(false);
    if(st.snapshot.stages && st.snapshot.stages.length){
      renderStageSkeleton(st.snapshot.stages);
      st.snapshot.stages.forEach(s=>{ S.stages[s.id] = {...(S.stages[s.id]||{}), ...s}; paintStage(s.id); });
      $("#bigBar").style.width = Math.round((st.snapshot.progress||0)*100)+"%";
      $("#bigPct").textContent = Math.round((st.snapshot.progress||0)*100)+" %";
    }
    // l'historique complet arrive par le flux d'événements
    if(!st.running && st.snapshot.finished){ /* le done sera réémis via history */ }
  }
})();
