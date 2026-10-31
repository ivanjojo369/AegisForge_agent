'use strict';
async function refresh(){
 const box=document.getElementById('wrap');
 try{
  const response=await fetch('/api/state',{credentials:'omit',cache:'no-store'});
  if(!response.ok)throw new Error('unavailable');
  const data=await response.json();
  const effect=data.effect;
  if(!effect){box.classList.add('hidden');return;}
  // Never interpret data as markup, a stylesheet or executable code.
  const visual=effect.visual||{};
  const themes=['teal','violet','amber','blue'];
  box.dataset.theme=themes.includes(visual.theme)?visual.theme:'teal';
  document.getElementById('scope').textContent=effect.preview_only?
   'LIVE FORGE · VISTA PREVIA DEL STREAMER':'LIVE FORGE · MOD COMUNITARIO';
  document.getElementById('title').textContent=effect.name||'';
  document.getElementById('message').textContent=effect.message||'';
  document.getElementById('components').textContent=Array.isArray(effect.components)?
   'Mecánicas combinadas: '+effect.components.map(c=>c.name).join(' + '):'';
  const total=Number(visual.duration_seconds);
  const left=Number(effect.seconds_remaining);
  const valid=Number.isFinite(total)&&total>0&&Number.isFinite(left);
  const pct=valid?Math.max(0,Math.min(100,100*left/total)):0;
  document.getElementById('timerFill').style.width=pct.toFixed(1)+'%';
  document.getElementById('timerBar').setAttribute('aria-valuenow',String(Math.round(pct)));
  document.getElementById('remaining').textContent=valid?
   'Tiempo restante del efecto: '+Math.ceil(Math.max(0,left))+' s':'';
  box.classList.remove('hidden');
 }catch(_){box.classList.add('hidden');}
}
refresh();setInterval(refresh,1000);
