'use strict';
// Bearer credential stays in page memory only. Never persist, log, or echo it.
let bearer = '';
let chosen = '';
let selectionEpoch = 0;
let archiveRows = [];
const $ = id => document.getElementById(id);
const routes = {detail:'', history:'/history', preview:'/preview', inspect:'/inspect'};
const monitors = {graph:'graph-view', laboratory:'lab-view', trust:'trust-view', timeline:'timeline-view'};
function status(msg){$('message').textContent = msg;}
function elem(tag, className, text){const el=document.createElement(tag);if(className)el.className=className;if(text!==undefined)el.textContent=String(text);return el;}
function add(parent,tag,className,text){const child=elem(tag,className,text);parent.appendChild(child);return child;}
function clearNode(node){node.replaceChildren();}
function unavailable(node,msg){clearNode(node);add(node,'div','empty',msg);}
function listRow(parent,left,right){const row=add(parent,'div','data-row');add(row,'span','',left);add(row,'strong','',right);return row;}
function metric(parent,value,label){const cell=add(parent,'div','metric-box');add(cell,'strong','',value);add(cell,'span','',label);return cell;}
function unavailableAll(message){for(const id of Object.values(monitors))unavailable($(id),message);}
function selected(id){
  chosen=id;const epoch=++selectionEpoch;
  resetArchive(id?'Consultando cierres históricos…':'Selecciona una misión.');
  document.querySelectorAll('.mission').forEach(el=>el.classList.toggle('active',el.dataset.id===id));
  for(const k of Object.keys(routes))$(k).disabled=!id;
  $('view-mission').textContent=id||'Sin selección';
  $('result').textContent=id?'Selecciona una consulta o explora las vistas inferiores.':'Selecciona una misión para inspeccionar sus metadatos.';
  if(!id){unavailableAll('Selecciona una misión para consultar sus fuentes.');return;}
  loadArchiveList(id,epoch).catch(e=>{
    if(epoch===selectionEpoch && chosen===id)unavailable($('archive-summary'),e.message||'No se pudo consultar el historial.');
  });
  for(const key of Object.keys(monitors)){
    const container=$(monitors[key]);unavailable(container,'Consultando '+key+'…');
    query(`/api/v1/missions/${encodeURIComponent(id)}/${key}`)
      .then(data=>{if(epoch===selectionEpoch && chosen===id)renderMonitor(key,data);})
      .catch(e=>{if(epoch===selectionEpoch && chosen===id)unavailable(container,e.message||'No se pudo consultar la fuente.');});
  }
}
function clear(){bearer='';chosen='';selectionEpoch++;resetArchive('Conéctate para consultar generaciones históricas.');$('secret').value='';$('connection').textContent='Sin conexión';$('count').textContent='—';$('result').textContent='Conexión cerrada.';$('missions').replaceChildren();$('refresh').disabled=true;$('product-filter').disabled=true;$('checkpoint-info').textContent='Origen del registro: no consultado.';for(const k of Object.keys(routes))$(k).disabled=true;$('view-mission').textContent='Sin selección';unavailableAll('Conéctate para consultar las fuentes.');status('Token descartado de esta sesión.');}
async function query(path){
  if(!bearer)throw new Error('Autenticación requerida.');
  const response=await fetch(path,{method:'GET',headers:{Authorization:`Bearer ${bearer}`,Accept:'application/json'},credentials:'omit',cache:'no-store',redirect:'error'});
  if(!response.ok){
    if(response.status===401){clear();throw new Error('Token inválido o caducado.');}
    if(response.status===409){const problem=await response.json().catch(()=>({}));if(problem.error==='workflow_not_configured')throw new Error('Esta misión no tiene un workflow configurado.');if(problem.error==='monitor_source_inconsistent')throw new Error('La fuente no superó las comprobaciones de integridad.');if(problem.error==='checkpoint_changed_restart_required')throw new Error('El checkpoint cambió o dejó de ser válido. Reinicia el servidor para cargarlo explícitamente.');if(problem.error==='source_archive_changed_restart_required')throw new Error('El archivo de fuentes persistidas cambió o no es válido. Reinicia el servidor tras revisarlo.');if(problem.error==='history_changed_restart_required')throw new Error('El historial cambió o no supera la verificación. Reinicia el servidor para volver a cargarlo.');}
    if(response.status===403)throw new Error('Consulta denegada por los permisos del operador.');
    if(response.status===404)throw new Error('Misión o recurso no disponible.');
    throw new Error(`Consulta no disponible (HTTP ${response.status}).`);
  }
  const body=await response.json();return body.data;
}
async function refresh(){
  const product=$('product-filter').value;
  const data=await query('/api/v1/missions?limit=50'+(product?'&product='+encodeURIComponent(product):''));
  $('missions').replaceChildren();const rows=Array.isArray(data.missions)?data.missions:[];
  $('count').textContent=String(rows.length);$('refresh').disabled=false;$('product-filter').disabled=false;
  if(!rows.length)add($('missions'),'p','quiet','No hay misiones visibles en este registro.');
  for(const row of rows){const btn=add($('missions'),'button','mission',row.mission_id);btn.dataset.id=row.mission_id;add(btn,'span','',`${row.product} · ${row.status}`);btn.addEventListener('click',()=>selected(row.mission_id));}
  if(chosen && rows.some(row=>row.mission_id===chosen))selected(chosen);else selected('');
}
function renderMonitor(kind,data){
  const box=$(monitors[kind]);clearNode(box);
  if(!data || data.available===false){unavailable(box,'Fuente no configurada para esta misión. El panel no genera datos de ejemplo para misiones reales.');return;}
  if(data.archived_metadata_only)add(box,'p','note','ARCHIVO HISTÓRICO: resumen vinculado al checkpoint, no una comprobación actual de la fuente.');
  if(kind==='graph')renderGraph(box,data);
  else if(kind==='laboratory')renderLab(box,data);
  else if(kind==='trust')renderTrust(box,data);
  else if(kind==='timeline')renderTimeline(box,data);
}
function renderGraph(box,data){
  const strip=add(box,'div','metric-strip');metric(strip,data.node_count,'Nodos');metric(strip,data.edge_count,'Relaciones');metric(strip,'SHA-256','Verificado');
  add(box,'div','list-heading','Nodos');
  const nodes=add(box,'div','graph-nodes');
  const labels=new Map();
  for(const node of (data.nodes||[])){
    const short=node.node_id.slice(0,9);labels.set(node.node_id,`${node.kind} · ${short}`);
    const chip=add(nodes,'div','graph-node');add(chip,'strong','',node.kind+' · '+short);add(chip,'small','',node.status+' / '+node.stage);
  }
  add(box,'div','list-heading','Relaciones declaradas');const links=add(box,'div','graph-links');
  if(!(data.edges||[]).length)add(links,'p','quiet','No hay relaciones registradas.');
  for(const edge of (data.edges||[]))add(links,'div','graph-link',`${labels.get(edge.source_node_id)||'origen'} → ${edge.relation} → ${labels.get(edge.target_node_id)||'destino'}`);
  add(box,'p','note',data.archived_metadata_only?'Grafo archivado: integridad comprobada durante la captura; no se recuperó el objeto original.':'Fuente verificada estructuralmente. Los enlaces expresan relaciones registradas, no autenticidad de evidencia.');
}
function renderLab(box,data){
  const a=data.aggregate||{};const strip=add(box,'div','metric-strip');
  metric(strip,a.passed??0,'Aprobadas');metric(strip,a.failed??0,'Fallidas');metric(strip,a.inconclusive??0,'Inconclusas');
  const total=Math.max(1,a.evaluations||0);const progress=add(box,'div','progress');
  for(const [key,classname] of [['passed','passed'],['failed','failed'],['inconclusive','inconclusive']]){
    const part=add(progress,'div',classname);part.style.width=`${Math.max(0,Math.min(100,100*(a[key]||0)/total))}%`;
  }
  listRow(box,'Observaciones',a.evaluations??0);listRow(box,'Informes de evaluación',data.evaluation_reports??0);
  listRow(box,'Comparaciones de regresión',data.regression_reports??0);listRow(box,'Estudios de ablación',data.ablation_studies??0);
  add(box,'div','list-heading','Distribución por producto');
  for(const [product,count] of Object.entries(data.by_product||{}))listRow(box,product,`${count.passed} ✓ · ${count.failed} ✕ · ${count.inconclusive} ?`);
  if((data.review_flags||[]).length){add(box,'div','list-heading','Alertas del informe');const flags=add(box,'div','review-flags');for(const flag of data.review_flags)add(flags,'span','pill warn',flag);}
  add(box,'p','note',data.archived_metadata_only?'Métricas históricas verificadas al capturarlas; los reportes originales no se vuelven a comprobar tras reiniciar.':'Cifras basadas en observaciones de reportes, no en escenarios únicos. No certifican precisión real ni causalidad.');
}
function renderTrust(box,data){
  const strip=add(box,'div','metric-strip');metric(strip,data.count??0,'Solicitudes');metric(strip,(data.counts||{}).pending??0,data.historical_only?'Pendientes al guardar':'Pendientes');metric(strip,(data.counts||{}).approved??0,data.historical_only?'Aprobadas al guardar':'Estado aprobado');
  if(!(data.requests||[]).length)add(box,'div','empty','No hay solicitudes de revisión configuradas.');
  else for(const request of data.requests){const item=add(box,'div','data-row');const left=add(item,'div');add(left,'strong','',request.operation);add(left,'div','monoline',request.request_sha256.slice(0,18)+'…');add(item,'span','pill'+(request.status==='pending'?' warn':''),request.status);}
  add(box,'p','note',data.historical_only?'ESTADOS HISTÓRICOS INERTES: ningún permiso, aprobación o validez actual se restaura desde el archivo.':'Solo consulta de estado. Incluso una solicitud marcada como aprobada NO concede permiso de ejecución en este panel. El Gate puede marcar solicitudes vencidas al consultarlas.');
}
function renderTimeline(box,data){
  const events=data.events||[];if(!events.length){add(box,'p','quiet','Todavía no hay eventos de misión.');return;}
  const list=add(box,'ol','timeline');
  for(const event of [...events].reverse()){
    const item=add(list,'li');add(item,'strong','',event.operation||'evento');
    add(item,'span','',`${event.status||'unknown'}${event.component?' · '+event.component:''}`);
    const time=add(item,'time','',event.at_utc||'');if(event.at_utc)time.dateTime=event.at_utc;
  }
  add(box,'p','note','Orden cronológico tomado del historial autorizado de Mission Registry. Las marcas temporales son las registradas por el host.');
}

function resetArchive(message){
  archiveRows=[];
  const sel=$('archive-generation');sel.disabled=true;sel.replaceChildren();
  add(sel,'option','', 'Sin selección').value='';
  $('archive-summary').textContent=message;
  unavailable($('archive-trend'),'Sin mediciones históricas disponibles.');
  for(const kind of ['graph','laboratory','trust'])unavailable($('archive-'+kind),'Selecciona una generación archivada.');
}
async function loadArchiveList(id,epoch){
  const data=await query(`/api/v1/missions/${encodeURIComponent(id)}/archives`);
  if(epoch!==selectionEpoch || chosen!==id)return;
  if(!data.available){resetArchive('Historial multigeneracional no configurado en este servidor.');return;}
  archiveRows=Array.isArray(data.generations)?data.generations:[];
  if(!archiveRows.length){resetArchive('Esta misión todavía no tiene cierres archivados.');return;}
  $('archive-summary').textContent=`${archiveRows.length} generación(es) archivada(s). Datos históricos, integridad local, autenticidad de origen no comprobada.`;
  const sel=$('archive-generation');sel.replaceChildren();
  for(const row of archiveRows){const opt=add(sel,'option','',`Generación ${row.generation} · ${row.kinds.join(', ')}`);opt.value=String(row.generation);}
  sel.disabled=false;
  renderArchiveTrend(data.lab_trend||[]);
  await loadArchiveGeneration(id,Number(sel.value),epoch);
}
function renderArchiveTrend(series){
  const el=$('archive-trend');clearNode(el);
  if(!series.length){unavailable(el,'No hay evaluaciones de Laboratory archivadas para comparar.');return;}
  for(const row of series){
    const total=Math.max(1,Number(row.evaluations)||0);
    const line=add(el,'div','trend-row');
    add(line,'strong','',`Gen. ${row.generation}`);
    const bars=add(line,'div','trend-bars');
    for(const [key,cls] of [['passed','trend-passed'],['failed','trend-failed'],['inconclusive','trend-inconclusive']]){
      const part=add(bars,'div',cls);part.style.width=`${Math.max(0,Math.min(100,100*(Number(row[key])||0)/total))}%`;
    }
    add(line,'span','',`${row.passed} ✓ · ${row.failed} ✕ · ${row.inconclusive} ?`);
  }
  add(el,'p','note','Comparación descriptiva: los casos, fuentes y criterios de evaluación pueden diferir entre generaciones.');
}
async function loadArchiveGeneration(id,generation,epoch){
  const selectedGen=$('archive-generation').value;
  if(selectedGen!==String(generation))return;
  for(const kind of ['graph','laboratory','trust'])unavailable($('archive-'+kind),'Leyendo resumen histórico…');
  const results=await Promise.allSettled(['graph','laboratory','trust'].map(
    kind=>query(`/api/v1/missions/${encodeURIComponent(id)}/archives/${generation}/${kind}`)));
  if(epoch!==selectionEpoch || chosen!==id || $('archive-generation').value!==String(generation))return;
  for(const [index,kind] of ['graph','laboratory','trust'].entries()){
    const dest=$('archive-'+kind);const r=results[index];
    if(r.status==='rejected'){unavailable(dest,r.reason.message||'Error al cargar.');continue;}
    const data=r.value;
    if(!data || data.available===false){unavailable(dest,'No existe resumen histórico de esta fuente.');continue;}
    clearNode(dest);
    add(dest,'p','note',`Generación ${generation} · ARCHIVO HISTÓRICO INERTE`);
    if(kind==='graph')renderGraph(dest,data);
    if(kind==='laboratory')renderLab(dest,data);
    if(kind==='trust')renderTrust(dest,data);
  }
}
$('archive-generation').addEventListener('change',()=>{
  if(!chosen)return;
  const id=chosen,epoch=selectionEpoch,generation=Number($('archive-generation').value);
  loadArchiveGeneration(id,generation,epoch).catch(e=>{
    if(epoch===selectionEpoch && chosen===id)unavailable($('archive-summary'),e.message||'No se pudo consultar la generación.');
  });
});

$('connect').addEventListener('click',async()=>{const input=$('secret').value;if(input.length<32){status('El token debe tener al menos 32 caracteres.');return;}bearer=input;$('secret').value='';try{const data=await query('/api/v1/status');$('connection').textContent=data.api_state==='ready'?'Conectado':'No disponible';await refresh();const checkpoint=await query('/api/v1/checkpoint');$('checkpoint-info').textContent=checkpoint.available?`Checkpoint validado · generación ${checkpoint.generation} · reinicio necesario para recargar`:'Registro en memoria · sin checkpoint cargado';if(checkpoint.source_archive?.configured)$('checkpoint-info').textContent+=' · fuentes históricas vinculadas';if(checkpoint.historical_generations_configured)$('checkpoint-info').textContent+=' · historial multigeneracional';status('Conexión autenticada. Panel de solo lectura.');}catch(e){clear();status(e.message||'No se pudo conectar.');}});
$('disconnect').addEventListener('click',clear);
$('product-filter').addEventListener('change',async()=>{try{await refresh();}catch(e){status(e.message||'No se pudo filtrar.');}});
$('refresh').addEventListener('click',async()=>{try{await refresh();status('Lista y vistas actualizadas.');}catch(e){status(e.message||'Error de actualización.');}});
for(const [key,suffix] of Object.entries(routes)){
  $(key).addEventListener('click',async()=>{if(!chosen)return;const id=chosen;try{const data=await query(`/api/v1/missions/${encodeURIComponent(id)}${suffix}`);if(chosen!==id)return;$('result').textContent=JSON.stringify(data,null,2);status(`Consulta ${key} completada.`);}catch(e){if(chosen===id){const msg=e.message||'No se pudo completar la consulta.';status(msg);$('result').textContent=msg;}}});
}
unavailableAll('Conéctate y selecciona una misión para ver su observatorio.');
resetArchive('Conéctate para consultar los cierres históricos.');
