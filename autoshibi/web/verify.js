/* Angora Studio — source-time cut editor, no video rendering. */
(()=>{
'use strict';
const $=id=>document.getElementById(id), panel=$('cutEditor'), canvas=$('cutCanvas'), ctx=canvas.getContext('2d'), video=$('cutVideo');
let session=null, segments=[], original=[], history=[], future=[], selected=-1, cursor=0, zoom=1, start=0, drag=null, loading=false, pending=false, wasActive=false;
const clone=x=>JSON.parse(JSON.stringify(x));
const time=x=>{const m=Math.floor(Math.max(0,x)/60),s=Math.max(0,x)%60;return `${m}:${s.toFixed(1).padStart(4,'0')}`;};
function partition(keep,duration){
 const ranges=keep.slice().sort((a,b)=>a[0]-b[0]), merged=[];
 for(const [a,b] of ranges){if(merged.length && a<=merged.at(-1)[1]+.001) merged.at(-1)[1]=Math.max(b,merged.at(-1)[1]);else merged.push([a,b]);}
 let out=[],pos=0;for(const [a,b] of merged){if(a>pos+.001)out.push({a:pos,b:a,keep:false});out.push({a,b,keep:true});pos=b;}
 if(pos<duration-.001)out.push({a:pos,b:duration,keep:false});return out;
}
function persist(){if(session)try{sessionStorage.setItem('angora-cuts-'+session.id,JSON.stringify({segments,selected,cursor}));}catch(_){} }
function snapshot(){return {segments:clone(segments),selected,cursor};}
function save(){history.push(snapshot());if(history.length>80)history.shift();future=[];}
function restore(x){segments=clone(x.segments);selected=x.selected;cursor=x.cursor;render();persist();}
function undo(){if(!history.length)return;future.push(snapshot());restore(history.pop());}
function redo(){if(!future.length)return;history.push(snapshot());restore(future.pop());}
function close(){panel.hidden=true;video.pause();video.removeAttribute('src');if(wasActive){if(window.updateTabTitle) window.updateTabTitle(null,'running');$('runSubtitle').textContent='Vérification terminée : reprise du montage.';$('chipEtaWrap').hidden=false;}session=null;wasActive=false;}
async function refresh(){
 if(loading)return;loading=true;
 try{
  const response=await fetch('/api/verify');if(!response.ok)return;const data=await response.json();
  if(!data.active){if(wasActive)close();return;}
  if(!session || session.id!==data.id){
   session=data;original=partition(data.keep,data.duration);segments=clone(original);history=[];future=[];selected=0;cursor=0;zoom=1;start=0;
   try{const saved=JSON.parse(sessionStorage.getItem('angora-cuts-'+data.id));if(saved){segments=saved.segments;selected=saved.selected;cursor=saved.cursor;}}catch(_){}
   video.src='/api/verify/source?id='+encodeURIComponent(data.id);pending=false;$('cutCommit').disabled=false;
   panel.hidden=false;wasActive=true;
   if(window.updateTabTitle) window.updateTabTitle(null,"paused");
   $('viewSetup').hidden=true;$('viewRun').hidden=false;
   $('runSubtitle').textContent='En pause : vérifie le dérush avant la génération des shibis et zooms.';
   $('chipEtaWrap').hidden=true;
   render();panel.scrollIntoView({behavior:'smooth',block:'start'});
  }else{session=data;draw();}
  $('cutWaveStatus').textContent=data.wave_status==='ready'?'Forme d’onde prête · glisse les frontières ; aimantation aux silences à ±0,35 s.':data.wave_status==='loading'?'Timeline utilisable immédiatement · forme d’onde audio en préparation…':'Forme d’onde indisponible. Tu peux tout de même ajuster et valider les coupes.';
 }catch(_){}finally{loading=false;}
}
function dims(){return {w:canvas.clientWidth,h:170,span:session.duration/zoom};}
function at(x){const d=dims();return Math.min(session.duration,Math.max(0,start+x/d.w*d.span));}
function xof(t){const d=dims();return (t-start)/d.span*d.w;}
function draw(){
 if(!session || panel.hidden)return;
 const {w,h,span}=dims(),ratio=window.devicePixelRatio||1;canvas.width=Math.max(1,w*ratio);canvas.height=h*ratio;ctx.setTransform(ratio,0,0,ratio,0,0);
 const dark=document.documentElement.dataset.theme==='dark';ctx.fillStyle=dark?'#22221f':'#f5f3ed';ctx.fillRect(0,0,w,h);
 for(let i=0;i<segments.length;i++){
  const s=segments[i],x=Math.max(0,xof(s.a)),end=Math.min(w,xof(s.b));if(end<x||end<0||x>w)continue;
  ctx.fillStyle=s.keep?(dark?'#244936':'#d7ecdf'):(dark?'#373630':'#e5e2d9');ctx.fillRect(x,28,end-x,115);
  if(!s.keep){ctx.save();ctx.beginPath();ctx.rect(x,28,end-x,115);ctx.clip();ctx.strokeStyle=dark?'#4b4941':'#ccc8bb';for(let k=x-115;k<end;k+=12){ctx.beginPath();ctx.moveTo(k,143);ctx.lineTo(k+115,28);ctx.stroke();}ctx.restore();}
  if(end-x>65){ctx.fillStyle=s.keep?(dark?'#9cdeb4':'#275e3d'):(dark?'#c5c0b3':'#777162');ctx.font='11px sans-serif';ctx.fillText(s.keep?'✓ Gardé':'− Coupé',x+8,47);}
 }
 const peaks=session.peaks||[];ctx.strokeStyle=dark?'#b6b4a1':'#797b6c';ctx.beginPath();
 for(let px=0;px<w;px+=2){let i=Math.floor((start+px/w*span)/session.duration*peaks.length),j=Math.max(i+1,Math.ceil((start+(px+2)/w*span)/session.duration*peaks.length)),p=0;for(;i<j&&i<peaks.length;i++)p=Math.max(p,peaks[i]||0);ctx.moveTo(px,94-p*31);ctx.lineTo(px,94+p*31);}ctx.stroke();
 ctx.fillStyle=dark?'#b4afa2':'#756f63';ctx.font='11px monospace';const step=span/6;for(let i=0;i<=6;i++){const t=start+i*step,x=i*w/6;ctx.fillText(time(t),Math.min(w-55,Math.max(2,x)),17);}
 for(let i=1;i<segments.length;i++){const x=xof(segments[i].a);if(x<0||x>w)continue;ctx.strokeStyle=dark?'#aaa28f':'#938a79';ctx.beginPath();ctx.moveTo(x,27);ctx.lineTo(x,144);ctx.stroke();ctx.fillStyle=dark?'#b8ad98':'#938a79';ctx.fillRect(x-3,54,6,18);}
 if(selected>=0&&segments[selected]){const s=segments[selected],x=xof(s.a),end=xof(s.b);ctx.strokeStyle='#df865e';ctx.lineWidth=2;ctx.strokeRect(Math.max(1,x+1),29,Math.max(0,Math.min(w-1,end-1)-Math.max(1,x+1)),113);ctx.lineWidth=1;}
 const x=xof(cursor);if(x>=0&&x<=w){ctx.strokeStyle='#e08156';ctx.beginPath();ctx.moveTo(x,22);ctx.lineTo(x,156);ctx.stroke();ctx.fillStyle='#e08156';ctx.fillRect(x-4,22,8,5);}
}
function render(){
 if(!session)return;const kept=segments.filter(s=>s.keep).reduce((n,s)=>n+s.b-s.a,0),s=segments[selected];
 $('cutSummary').textContent=`${time(kept)} gardées · ${time(session.duration-kept)} coupées · ${segments.filter(s=>s.keep).length} zones conservées`;
 $('cutSelection').textContent=s?`${s.keep?'Gardé':'Coupé'} · ${time(s.a)} → ${time(s.b)} · ${(s.b-s.a).toFixed(2)} s`:'Sélectionne un segment';
 $('cutToggle').textContent=s?(s.keep?'✂ Couper ce passage':'↩ Garder ce passage'):'Garder / Couper';$('cutToggle').disabled=!s;$('cutToggle').dataset.keep=s?String(!s.keep):'';
 $('cutUndo').disabled=!history.length;$('cutRedo').disabled=!future.length;
 const span=session.duration/zoom;start=Math.max(0,Math.min(start,session.duration-span));
 $('cutZoomLabel').textContent=zoom===1?'Vue entière':`×${zoom}`;$('cutViewStart').textContent=time(start);$('cutViewEnd').textContent=time(start+span);
 const pan=zoom===1?0:start/(session.duration-span)*1000;
 $('cutPan').disabled=zoom===1;$('cutPan').value=pan;$('cutPan').style.setProperty('--fill',`${pan/10}%`);draw();
}
function seek(t){cursor=t;if(video.readyState)try{video.currentTime=t;}catch(_){}draw();persist();}
function select(i){selected=Math.max(0,Math.min(segments.length-1,i));const s=segments[selected],span=session.duration/zoom;if(s.a<start||s.a>start+span)start=Math.max(0,s.a-span*.15);seek(s.a);render();}
function segmentAt(t){return segments.findIndex((s,i)=>t>=s.a&&(t<s.b||(i===segments.length-1&&t<=s.b+.05)));}
function followPlayback(){
 if(!session||drag)return;
 cursor=video.currentTime;
 const next=segmentAt(cursor),span=session.duration/zoom;
 let changed=next>=0&&next!==selected;
 if(next>=0)selected=next;
 if(cursor<start||cursor>start+span){start=Math.max(0,Math.min(session.duration-span,cursor-span*.15));changed=true;}
 if(changed)render();else draw();
}
function toggle(keep=null){
 if(pending||!session)return;
 // Use the real playback time, even between two native timeupdate events.
 if(!video.paused){const i=segmentAt(video.currentTime);if(i>=0){selected=i;cursor=video.currentTime;}}
 if(selected<0)return;
 const desired=keep===null?!segments[selected].keep:keep;
 if(segments[selected].keep===desired){render();return;}
 save();segments[selected].keep=desired;render();persist();
}
function split(){if(pending)return;const i=segments.findIndex(s=>cursor>s.a+.04&&cursor<s.b-.04);if(i<0){$('cutError').textContent='Place le curseur à l’intérieur d’un segment pour le scinder.';return;}save();const s=segments[i];segments.splice(i,1,{a:s.a,b:cursor,keep:s.keep},{a:cursor,b:s.b,keep:s.keep});selected=i+1;render();persist();}
function zoomBy(factor){if(!session)return;const center=start+session.duration/zoom/2;zoom=Math.max(1,Math.min(128,zoom*factor));start=center-session.duration/zoom/2;render();}
canvas.addEventListener('pointerdown',e=>{
 if(!session||pending)return;const rect=canvas.getBoundingClientRect(),x=e.clientX-rect.left,y=e.clientY-rect.top;canvas.focus();let boundary=-1,best=8;
 if(y>25)for(let i=1;i<segments.length;i++){const d=Math.abs(xof(segments[i].a)-x);if(d<best){best=d;boundary=i;}}
 if(boundary>=0){drag={index:boundary,before:snapshot()};canvas.setPointerCapture(e.pointerId);}
 else{const t=at(x);selected=segments.findIndex(s=>t>=s.a&&t<=s.b);seek(t);render();}
});
canvas.addEventListener('pointermove',e=>{
 if(!session)return;const x=e.clientX-canvas.getBoundingClientRect().left;
 if(!drag){canvas.style.cursor=segments.slice(1).some(s=>Math.abs(xof(s.a)-x)<8)?'ew-resize':'crosshair';return;}
 const i=drag.index,left=segments[i-1],right=segments[i];let t=at(x),snapped=false;
 if($('cutSnap').checked){let best=.35;for(const [a,b] of session.silences){const mid=(a+b)/2,d=Math.abs(mid-t);if(d<best&&mid>left.a+.04&&mid<right.b-.04){t=mid;best=d;snapped=true;}}}
 const margin=Math.min(.04,(right.b-left.a)/3);
 t=Math.max(left.a+margin,Math.min(right.b-margin,t));left.b=t;right.a=t;cursor=t;selected=i;
 $('cutSnapHint').textContent=snapped?'🧲 Frontière aimantée au centre d’un silence':'Ajustement libre · aimantation ±0,35 s si activée';render();
});
function endDrag(){if(!drag)return;history.push(drag.before);future=[];drag=null;render();persist();}
canvas.addEventListener('pointerup',endDrag);canvas.addEventListener('pointercancel',endDrag);
$('cutToggle').onclick=()=>toggle($('cutToggle').dataset.keep==='true');$('cutSplit').onclick=split;$('cutUndo').onclick=undo;$('cutRedo').onclick=redo;
$('cutPrev').onclick=()=>select(selected-1);$('cutNext').onclick=()=>select(selected+1);
$('cutZoomIn').onclick=()=>zoomBy(2);$('cutZoomOut').onclick=()=>zoomBy(.5);
$('cutPan').oninput=()=>{if(session){start=+$('cutPan').value/1000*(session.duration-session.duration/zoom);render();}};
$('cutReset').onclick=()=>{if(confirm('Revenir aux coupes proposées par l’IA ?')){save();segments=clone(original);selected=0;render();persist();}};
$('cutCommit').onclick=async()=>{
 if(!session||pending)return;const keep=segments.filter(s=>s.keep).map(s=>[s.a,s.b]);if(!keep.length){$('cutError').textContent='Garde au moins un passage.';return;}
 pending=true;$('cutCommit').disabled=true;$('cutError').textContent='';
 try{const r=await fetch('/api/verify/commit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:session.id,keep})}),data=await r.json();if(!data.ok)throw Error(data.error);sessionStorage.removeItem('angora-cuts-'+session.id);close();$('runSubtitle').textContent='Coupes validées : génération des shibis et zooms sur les passages conservés.';$('chipEtaWrap').hidden=false;}catch(e){$('cutError').textContent=String(e);pending=false;$('cutCommit').disabled=false;}
};
video.addEventListener('loadedmetadata',()=>{if(session&&cursor>0)try{video.currentTime=cursor;}catch(_){}});
video.addEventListener('error',()=>{$('cutVideoHint').textContent='Ce codec n’est pas lisible dans ce navigateur. La timeline reste utilisable ; aucune conversion vidéo n’est lancée.';});
video.addEventListener('timeupdate',()=>{
 if(!session||drag)return;
 const current=video.currentTime;
 if($('cutSkip').checked&&!video.paused){
  const i=segmentAt(current),s=segments[i];
  if(s&&!s.keep){const next=segments.find(x=>x.keep&&x.a>=s.b-.001);if(next){video.currentTime=next.a;}else video.pause();}
 }
 followPlayback();
});
video.addEventListener('seeked',followPlayback);
video.addEventListener('ended',followPlayback);
panel.addEventListener('keydown',e=>{
 if(e.target.matches('input,video'))return;
 if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();e.shiftKey?redo():undo();}
 if(e.key==='ArrowLeft'){e.preventDefault();select(selected-1);}if(e.key==='ArrowRight'){e.preventDefault();select(selected+1);}
 if(e.key==='Delete'||e.key==='Backspace'){e.preventDefault();toggle();}
 if(e.key===' '&&e.target===canvas){e.preventDefault();video.paused?video.play().catch(()=>{}):video.pause();}
});
new ResizeObserver(draw).observe(canvas);new MutationObserver(draw).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
setInterval(()=>{if(!$('viewRun').hidden)refresh();},2000);
window.CutEditor={refresh,close};
})();
