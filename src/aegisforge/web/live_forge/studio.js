'use strict';
// Public, read-only local scene; does not accept tokens, chat or raw HTML.
const byId=id=>document.getElementById(id);
const paletteNames={teal:'Turquesa',violet:'Violeta',amber:'Ámbar',blue:'Azul'};
const shapeGlyphs={star:'✦',circle:'●',diamond:'◆',leaf:'✿',spark:'✧',moon:'☾',sun:'☼',heart:'♥'};
const supportedSceneSchemas=new Set(['aegisforge.live_forge.creative_studio.v1','aegisforge.live_forge.creative_studio.v2','aegisforge.live_forge.creative_studio.v3']);
let lastRevision=-1;
function listText(id, rows, emptyText) {
 const node=byId(id);node.replaceChildren();
 if (!Array.isArray(rows) || rows.length===0) {
  const li=document.createElement('li');li.textContent=emptyText;node.appendChild(li);return;
 }
 for(const row of rows){const li=document.createElement('li');li.textContent=row;node.appendChild(li);}
}
async function refresh(){
 try{
  const response=await fetch('/api/studio',{cache:'no-store',credentials:'omit'});
  if(!response.ok)throw new Error('offline');
  const data=await response.json();
  const scene=data.scene;
  const details=data.details;
  if(!scene || !supportedSceneSchemas.has(scene.schema) || !details ||
     !Array.isArray(details.objects) || !Array.isArray(details.objectives) ||
     !Array.isArray(details.history))throw new Error('invalid_scene');
  byId('status').textContent='Conectado · lectura pública';
  if(scene.revision===lastRevision)return;
  lastRevision=scene.revision;
  const stage=byId('stage');
  stage.dataset.palette=Object.hasOwn(paletteNames,scene.palette)?scene.palette:'teal';
  stage.dataset.setting=scene.setting==='night_sky'?'night_sky':'garden';
  stage.dataset.constraint=scene.constraint===true?'true':'false';
  byId('setting').textContent=scene.setting==='night_sky'?'Cielo de posibilidades':'Jardín creativo';
  byId('revision').textContent=String(scene.revision);
  const cap=scene.schema.endsWith('.v3')?24:0;
  byId('progress').textContent=String(scene.progress)+' / '+(cap||6);
  byId('count').textContent=String(scene.elements.length)+' / '+(cap||8);
  const container=byId('elements');container.replaceChildren();
  for(const element of scene.elements){
   const tile=document.createElement('div');tile.className='tile';
   tile.textContent=shapeGlyphs[element.shape]||'✦';
   tile.setAttribute('aria-label','Elemento '+String(element.id)+': '+String(element.shape));
   container.appendChild(tile);
  }
  const labels=[`Tema: ${paletteNames[scene.palette]||'Predeterminado'}`];
  if(scene.challenge)labels.push('Desafío virtual activo');
  if(scene.roles)labels.push('Roles cooperativos activos');
  if(scene.constraint)labels.push('Restricción creativa activa');
  byId('notice').textContent=scene.revision===0?'Sin cambios todavía. Autoriza una mecánica en la consola.':labels.join(' · ');
  listText('object-list', details.objects.map(o => 'Elemento '+o.id+': '+o.name+' · Paleta '+o.palette+' · '+o.setting), 'Sin figuras todavía.');
  listText('objective-list', details.objectives.map(o => o.title+': '+o.description), 'Sin objetivos completados todavía.');
  listText('studio-history', details.history.map(e => 'Revisión '+e.revision+': '+e.title+' — '+e.description), 'Aún no hay acciones registradas.');
 }catch(_){byId('status').textContent='Sin conexión con el estudio';}
}
refresh();setInterval(refresh,1500);
