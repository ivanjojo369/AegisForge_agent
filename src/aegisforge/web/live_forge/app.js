'use strict';
const el=id=>document.getElementById(id);
let catalog=[];
let compositions=[];
let activityContracts={};
let pendingComposition=null;
let operatorToken=''; // Only memory of this tab; never placed in an overlay or URL.
let formTouched=false;
let currentRound=0;
const operatorControls=['applyPolicy','preview','start','close','finish','stop','studioA','studioB','studioReset','bridgeA','bridgeB','bridgeReset','compose','exportEvidence'];
function syncCodexStart(){
 el('codexStart').disabled=!operatorToken || !el('codexConsent').checked || codexBusy;
}
let codexBusy=false;
function updateOperatorStatus(message){
 const ready=Boolean(operatorToken);
 for(const id of operatorControls)el(id).disabled=!ready;
 const label=el('operatorStatus');
 label.textContent=message || (ready?
  'Operador autorizado en esta pestaña. Los controles de Live Forge están disponibles.':
  'Sesión sin autorización. Al cerrar o reabrir la pestaña debes introducir otra vez el token local que aparece en PowerShell.');
 label.className='operator-notice '+(ready?'operator-ok':'operator-locked');
 syncCodexStart();
}
function restorePolicyOnOpen(saved){
 if(!saved || formTouched)return;
 for(const [id,value] of [['activity',saved.activity],['optionA',saved.option_a],['optionB',saved.option_b]]){
  const select=el(id);
  if(![...select.options].some(opt=>opt.value===value)){
   setMessage('La política activa incluye una opción que no existe en este catálogo: '+String(value));
   return;
  }
 }
 el('activity').value=saved.activity;
 el('optionA').value=saved.option_a;
 el('optionB').value=saved.option_b;
 el('enabled').checked=saved.enabled===true;
 el('cooldown').value=String(saved.cooldown_seconds);
 el('duration').value=String(saved.duration_seconds);
}
async function initialize(){
 updateOperatorStatus();
 await readCatalog();
 try{
  const response=await fetch('/api/state',{cache:'no-store',credentials:'omit'});
  if(response.ok)restorePolicyOnOpen((await response.json()).policy);
 }catch(_){setMessage('No se pudo recuperar la política activa. Comprueba el servidor.');}
 await refresh();
 setInterval(refresh,1500);
 setInterval(()=>{if(operatorToken)refreshCodex();},4000);
}
function setMessage(message){el('feedback').textContent=message;}
function studioErrorMessage(error){
 const messages={
  operator_auth_required:'Falta el token de operador: introdúcelo y pulsa «Usar token en esta pestaña».',
  policy_paused:'La política está pausada: activa las rondas y pulsa «Guardar política».',
  mechanic_cooldown:'La pausa entre efectos aún está activa. Comprueba el contador junto a estos botones.',
  close_round_before_studio_change:'Hay una ronda abierta: ciérrala antes de modificar el estudio.',
  mechanic_not_allowed:'Esta mecánica no coincide con la política guardada. Pulsa «Guardar política».',
  studio_activity_not_supported:'El estudio de la Fase 6 solo admite la categoría «art».',
  studio_progress_limit:'Se alcanzó el máximo de objetivos del estudio (24 en escenas nuevas).',
  studio_element_limit:'Se alcanzó el máximo de figuras del estudio (24 en escenas nuevas).',
  studio_record_or_archive_unavailable:'No se pudo registrar y archivar la operación; por seguridad la escena no cambió. Revisa la consola PowerShell y el directorio de checkpoints.',
  bridge_activity_not_supported:'Arte utiliza el Estudio Creativo. Para Activity Bridge selecciona una de las otras seis categorías.',
  bridge_mechanic_not_supported:'La mecánica seleccionada no modifica este entorno virtual.',
  bridge_record_or_archive_unavailable:'La evidencia no se archivó; el entorno no cambió.',
  bridge_task_limit:'Límite de 24 tareas.',
  bridge_milestone_limit:'Límite de 24 hitos.',
  bridge_insight_limit:'Límite de 24 datos destacados.'
 };
 return messages[error] || ('Operación rechazada por el servidor: '+String(error));
}
function setStudioNotice(message, error=false){
 el('studioStatus').textContent=message;
 el('studioStatus').className=error?'fine studio-error':'fine studio-ok';
}

async function refresh(){
 try{
  const response=await fetch('/api/state',{cache:'no-store',credentials:'omit'});
  if(!response.ok)throw new Error('Servidor no disponible');
  const s=await response.json();
  currentRound=s.round;
  const pending=s.resolution==='pending_approval';
  const roundBusy=s.round_open || pending;
  const live=s.live_connected===true;
  el('connection').textContent=live?'Twitch conectado':`Twitch sin conectar · ${s.connection}`;
  el('connection').className='badge '+(live?'live':'offline');
  el('a').textContent=String(s.choice_a);
  el('b').textContent=String(s.choice_b);
  el('count').textContent=`Votantes únicos: ${s.unique_voters}`;
  el('bar').style.width=`${s.unique_voters?100*s.choice_a/s.unique_voters:50}%`;
  el('round').textContent=s.round_open?`Ronda ${s.round} abierta`:
   pending?`Ronda ${s.round}: ganó ${s.winner}, pendiente de autorización`:`Ronda ${s.round} · ${s.resolution}`;
  el('issue').textContent=s.last_issue_code?`Estado técnico: ${s.last_issue_code}`:'';
  const d=s.eventsub_diagnostics;
  el('eventsubStats').textContent=d?`EventSub ${d.connected?"conectado":"sin sesión activa"}: ${d.notifications_seen} notificaciones recibidas · ${d.votes_accepted} votos aceptados · ${d.ignored_or_duplicate} ignorados/duplicados · ${d.invalid_notifications} inválidos · ${d.reconnections} reconexiones. No se publican usuarios ni mensajes.`:'EventSub sin configurar: no se generan votos sintéticos ni se acepta su inyección HTTP.';
  const effect=s.effect;
  el('effect').textContent=effect?`${effect.preview_only?"VISTA PREVIA · ":""}${effect.name}: ${effect.message} (${effect.seconds_remaining}s)`:'Sin efecto activo';
  el('policyStatus').textContent=s.policy.enabled?'Política activa: efectos visuales autorizados':'Política pausada';
  const remaining=Number(s.studio_cooldown_remaining_seconds)||0;
  const unsaved=(s.policy.activity!==el('activity').value ||
   s.policy.option_a!==el('optionA').value || s.policy.option_b!==el('optionB').value ||
   s.policy.enabled!==el('enabled').checked ||
   s.policy.cooldown_seconds!==Number(el('cooldown').value) ||
   s.policy.duration_seconds!==Number(el('duration').value));
  el('studioAvailability').textContent=(!s.policy.enabled?'Estudio bloqueado: guarda una política habilitada.':
   unsaved?'Hay cambios en las opciones sin guardar: pulsa «Guardar política».':
   s.policy.activity!=='art'?'Estudio no disponible para esta categoría: selecciona «art» y guarda la política.':
   s.round_open?'Estudio bloqueado: cierra primero la ronda abierta.':
   remaining>0?`Pausa de seguridad: faltan aproximadamente ${remaining} s antes de aplicar otra mecánica.`:
   'Listo para aplicar A o B al estudio mediante autorización del operador.');
  const contract=activityContracts[s.policy.activity];
  for(const id of ['applyPolicy','preview','start','studioA','studioB','studioReset']){
   el(id).disabled=!operatorToken || roundBusy;
  }
  el('close').disabled=!operatorToken || !s.round_open;
  el('finish').disabled=!operatorToken || !pending || remaining>0;
  el('roundTarget').disabled=!operatorToken;
  const canBridge=contract?.target==='bridge';
  const validA=contract?.virtual_compatible?.includes(s.policy.option_a)===true;
  const validB=contract?.virtual_compatible?.includes(s.policy.option_b)===true;
  el('bridgeA').disabled=!operatorToken || !canBridge || !validA || !s.policy.enabled || unsaved || roundBusy || remaining>0;
  el('bridgeB').disabled=!operatorToken || !canBridge || !validB || !s.policy.enabled || unsaved || roundBusy || remaining>0;
  el('bridgeReset').disabled=!operatorToken || !canBridge || roundBusy;
  if(pending){
   el('studioAvailability').textContent='Ganador pendiente: autoriza su aplicación o usa PARAR TODO para descartarlo.';
   el('bridgeStatus').textContent='Ronda pendiente de autorización. La política y la actividad permanecen bloqueadas.';
  }
  el('bridgeStatus').textContent=(pending?'Ronda pendiente: autoriza el ganador o usa PARAR TODO.':
   !canBridge?'Esta categoría usa el estudio creativo, no Activity Bridge.':
   unsaved?'Hay opciones sin guardar: pulsa «Guardar política».':
   s.round_open?'Cierra la ronda primero.':
   remaining>0?`Pausa de seguridad: ${remaining} s.`:
   (!validA || !validB)?`Compatibilidad virtual: A ${validA?'admitida':'solo overlay'} · B ${validB?'admitida':'solo overlay'}. Las mecánicas solo overlay siguen disponibles para votaciones y efectos visuales.`:
   'A y B compatibles con la actividad virtual. Acciones solo con autorización del operador.');
  const evidence=s.latest_evidence;
  el('evidenceStatus').textContent=evidence?
   `${evidence.mission_id} · ${evidence.status} · ${evidence.checks} comprobaciones · ${evidence.archive.archived?'archivada · generación '+evidence.archive.generation:'volátil'}`:
   `Sin evidencia cerrada en esta sesión (${s.evidence_mode}).`;
  if(s.evidence_issue)el('evidenceStatus').textContent+=' · Archivo incompleto: requiere inspección del operador.';
 }catch(_){el('connection').textContent='Servidor no disponible';el('connection').className='badge offline';}
}
async function readCatalog(){
 try{
  const r=await fetch('/api/mechanics',{credentials:'omit'});
  if(!r.ok)throw new Error();
  const data=await r.json(); catalog=data.mechanics; compositions=data.compositions||[];
  activityContracts=Object.fromEntries((data.contracts||[]).map(c=>[c.activity,c]));
  const allOptions=[...catalog, ...compositions.map(c=>({...c,origin:'Mezcla permitida'}))];
  const parts=new Set(compositions.flatMap(c=>c.components));
  el('composerStats').textContent=`${catalog.length} mecánicas preaprobadas · ${compositions.length} mezclas compatibles`;
  for(const [target, options] of [['activity',data.activities],['optionA',allOptions],['optionB',allOptions],['composeFirst',catalog.filter(c=>parts.has(c.id))],['composeSecond',catalog.filter(c=>parts.has(c.id))]]){
   const select=el(target);select.replaceChildren();
   for(const opt of options){const element=document.createElement('option');
    element.value=typeof opt==='string'?opt:opt.id;
    element.textContent=typeof opt==='string'?opt:opt.name+' · '+opt.origin;
    select.appendChild(element);}
  }
  el('activity').value='art';el('optionA').value='rpg_quest';el('optionB').value='creative_palette';
  el('composeFirst').value='rpg_quest';el('composeSecond').value='creative_palette';
  updateComplementChoices();
 }catch(_){setMessage('No se pudo cargar el catálogo de mecánicas.');}
}
async function command(body){
 const studioAction=['studio_apply','studio_reset','bridge_apply','bridge_reset'].includes(body.action);
 if(!operatorToken){
  const msg='Introduce primero el token de operador mostrado en PowerShell.';
  setMessage(msg);if(studioAction){if(body.action.startsWith('bridge'))el('bridgeStatus').textContent=msg;else setStudioNotice(msg,true);}
  return null;
 }
 try{
  const response=await fetch('/api/control',{method:'POST',credentials:'omit',
   headers:{'Content-Type':'application/json','Authorization':'Bearer '+operatorToken},
   body:JSON.stringify(body)});
  const data=await response.json();
  if(!response.ok){
   if(response.status===403 && data.error==='operator_auth_required'){
    operatorToken='';
    updateOperatorStatus('El servidor no reconoce el token. Vuelve a introducir el token vigente de PowerShell.');
   }
   const msg=studioAction?studioErrorMessage(data.error||response.status):
    'Operación rechazada: '+String(data.error||response.status);
   setMessage(msg);if(studioAction){if(body.action.startsWith('bridge'))el('bridgeStatus').textContent=msg;else setStudioNotice(msg,true);}
   await refresh();return null;
  }
  if(data.evidence_error){
   setMessage('Acción local realizada, pero el registro de evidencias falló. El efecto fue detenido.');
  }else if(body.action==='compose'){
   setMessage('Composición permitida preparada, sin efectos. Selecciona A o B y guarda la política.');
  }else if(body.action==='close'){
   setMessage(data.round.winner?`Ronda cerrada. Ganó ${data.round.winner}; selecciona destino y autoriza su aplicación.`:
    'Ronda cerrada sin ganador (empate o cero votos); no se aplica ninguna mecánica.');
  }else if(body.action==='apply_winner' || body.action==='finish'){
   const w=data.round.winner;
   setMessage(!w?'Ronda cerrada sin ganador (empate o cero votos).':
    data.scene?`Ganó ${w}: Estudio Creativo actualizado, revisión ${data.scene.revision}.`:
    data.world?`Ganó ${w}: Activity Bridge actualizado, revisión ${data.world.revision}.`:
    data.effect?`Ganó ${w}: efecto visual activado con aprobación del operador.`:
    `Ganó ${w}, pero no se aplicó efecto: ${data.blocked_reason||'sin permiso'}.`);
  }else{setMessage('Acción de operador aceptada: '+body.action);}
  await refresh();
  return data;
 }catch(_){
  const msg='No se pudo contactar con Live Forge.';
  setMessage(msg);if(studioAction){if(body.action.startsWith('bridge'))el('bridgeStatus').textContent=msg;else setStudioNotice(msg,true);}
 }
 return null;
}
function updateComplementChoices(){
 const first=el('composeFirst').value;
 const allowed=new Set(compositions.filter(c=>c.components.includes(first))
  .flatMap(c=>c.components.filter(id=>id!==first)));
 const second=el('composeSecond');
 const old=second.value;
 for(const option of second.options){
  option.disabled=!allowed.has(option.value);
  option.hidden=!allowed.has(option.value);
 }
 if(!allowed.has(old)){
  const match=[...second.options].find(option=>allowed.has(option.value));
  second.value=match?match.value:'';
 }
}
function clearComposition(){
 pendingComposition=null;
 el('assignA').disabled=true;el('assignB').disabled=true;
 el('composeResult').textContent='Sin composición preparada para los valores actuales.';
}
for(const id of ['composeFirst','composeSecond','activity']){
 el(id).addEventListener('change',()=>{
  if(id==='composeFirst')updateComplementChoices();
  clearComposition();
 });
}
el('compose').addEventListener('click', async()=>{
 clearComposition();
 const result=await command({action:'compose',first:el('composeFirst').value,
  second:el('composeSecond').value,activity:el('activity').value});
 if(!result?.composition)return;
 pendingComposition=result.composition;
 const c=pendingComposition;
 el('composeResult').textContent=`${c.name}\n${c.message}\nSolo overlay; no se ha activado ni autorizado para una ronda.`;
 el('assignA').disabled=false;el('assignB').disabled=false;
});
for(const [button,select] of [['assignA','optionA'],['assignB','optionB']]){
 el(button).addEventListener('click',()=>{
  if(!pendingComposition)return;
  if(el('activity').value!==pendingComposition.activity){clearComposition();return;}
  el(select).value=pendingComposition.id;
  setMessage('Mezcla asignada a '+(button==='assignA'?'A':'B')+'. Guarda la política antes de abrir una ronda.');
 });
}
el('exportEvidence').addEventListener('click',async()=>{
 if(!operatorToken){setMessage('Introduce el token de operador para exportar evidencias.');return;}
 try{
  const response=await fetch('/api/evidence/latest',{cache:'no-store',credentials:'omit',
   headers:{'Authorization':'Bearer '+operatorToken}});
  if(!response.ok){
   if(response.status===403){
    operatorToken='';
    updateOperatorStatus('El token ya no es válido. Vuelve a introducir el token vigente del servidor.');
   }
   setMessage(response.status===403?'Se requiere volver a autenticar al operador.':'No existe evidencia exportable en esta sesión.');
   return;
  }
  const data=await response.json();
  const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});
  const url=URL.createObjectURL(blob);
  const link=document.createElement('a'); link.href=url;
  link.download='liveforge-'+data.mission.mission_id+'.json';
  document.body.appendChild(link);link.click();link.remove();
  URL.revokeObjectURL(url);
  setMessage('Evidencia agregada exportada, sin datos personales.');
 }catch(_){setMessage('No se pudo exportar la evidencia.');}
});
el('tokenButton').addEventListener('click',async()=>{
 const token=el('token').value.trim();
 if(token.length<24){updateOperatorStatus('Token incompleto. Comprueba el valor mostrado al arrancar el servidor.');return;}
 el('tokenButton').disabled=true;
 try{
  const response=await fetch('/api/operator/session',{
   credentials:'omit',cache:'no-store',headers:{'Authorization':'Bearer '+token}
  });
  if(!response.ok){
   operatorToken='';
   updateOperatorStatus('El token no coincide con el servidor actual. Copia el token vigente desde PowerShell.');
   return;
  }
  operatorToken=token;
  el('token').value='';
  updateOperatorStatus();
  setMessage('Autorización confirmada. Se recuperó la política del servidor sin modificarla.');
 }catch(_){updateOperatorStatus('No se pudo verificar el token. Comprueba que Live Forge siga encendido.');}
 finally{el('tokenButton').disabled=false;}
});
el('token').addEventListener('keydown',event=>{
 if(event.key==='Enter'){event.preventDefault();el('tokenButton').click();}
});
for(const id of ['activity','optionA','optionB','enabled','cooldown','duration']){
 el(id).addEventListener('change',()=>{formTouched=true;});
}
el('applyPolicy').addEventListener('click',()=>command({action:'policy',policy:{
 enabled:el('enabled').checked,activity:el('activity').value,
 option_a:el('optionA').value,option_b:el('optionB').value,
 cooldown_seconds:Number(el('cooldown').value),duration_seconds:Number(el('duration').value)
}}));
el('preview').addEventListener('click',()=>command({action:'preview',mechanic_id:el('optionA').value}));
el('start').addEventListener('click',()=>command({action:'start'}));
el('close').addEventListener('click',()=>command({action:'close'}));
el('finish').addEventListener('click',()=>command({action:'apply_winner',round:currentRound,target:el('roundTarget').value}));
for(const [id,option] of [['studioA','A'],['studioB','B']]){
 el(id).addEventListener('click',async()=>{
  setStudioNotice('Comprobando permisos y archivado de la operación…');
  const result=await command({action:'studio_apply',option});
  if(result?.scene){
   setStudioNotice(`Escena actualizada · revisión ${result.scene.revision} · ${result.transition.mechanic_id}. Abre el estudio virtual para ver los cambios.`);
   setMessage('Cambio local autorizado · '+(result.evidence?.archive?.archived?'evidencia archivada':'evidencia solo en sesión')+'. No procede de votos de Twitch.');
  }
 });
}
for(const [id,option] of [['bridgeA','A'],['bridgeB','B']]){
 el(id).addEventListener('click',async()=>{
  const response=await command({action:'bridge_apply',option});
  if(response?.world){
   const w=response.world;
   el('bridgeStatus').textContent=`${w.activity}: revisión ${w.revision} · tareas ${w.tasks}/24 · hitos ${w.milestones}/24 · datos ${w.insights}/24 · evidencia ${response.evidence?.archive?.archived?'archivada':'local'}.`;
  }
 });
}
el('bridgeReset').addEventListener('click',async()=>{
 if(!window.confirm('¿Reiniciar solo la actividad virtual seleccionada?'))return;
 const result=await command({action:'bridge_reset'});
 if(result?.world)el('bridgeStatus').textContent=`${result.world.activity} reiniciada; evidencia registrada.`;
});
el('studioReset').addEventListener('click',async()=>{
 if(!window.confirm('¿Reiniciar la escena local? Esta acción quedará registrada como operación del streamer.'))return;
 setStudioNotice('Comprobando archivado del reinicio…');
 const result=await command({action:'studio_reset'});
 if(result?.scene){setStudioNotice(`Escena reiniciada · revisión ${result.scene.revision}.`);
  setMessage('Estudio reiniciado · '+(result.evidence?.archive?.archived?'evidencia archivada':'evidencia solo en sesión')+'.');}
});
el('stop').addEventListener('click',()=>command({action:'stop'}));
async function refreshCodex(){
 if(!operatorToken){el('codexStatus').textContent='Autentica al operador para consultar Codex.';return;}
 try{
  const r=await fetch('/api/codex/status',{credentials:'omit',cache:'no-store',headers:{'Authorization':'Bearer '+operatorToken}});
  if(!r.ok)throw new Error('auth_or_server');
  const data=await r.json();
  const job=data.job;
  codexBusy=Boolean(job && ['queued','running'].includes(job.status));
  syncCodexStart();
  el('codexStatus').textContent=!data.cli_found?'Codex CLI no encontrado en PATH. Instálalo e inicia sesión localmente antes de utilizar este puente.':
   !job?'Codex disponible; sin ejecuciones en esta sesión.':
   `Trabajo ${job.job_id}: ${job.status}. Intento de CLI: ${job.codex_invoked?'sí':'no'}. ${job.error||''}`;
  el('codexResult').textContent=job?.status==='completed'?
    `Propuesta no aplicada · SHA-256: ${job.result_sha256}\n\n${job.result}`:
    job?.status==='failed'?'Codex no completó el análisis. Revisa la sesión local de Codex en la terminal, sin compartir credenciales.':
    'Sin propuesta completada. No hay cambios de código realizados por este puente.';
 }catch(_){el('codexStatus').textContent='No se pudo consultar el puente Codex.';}
}
el('codexConsent').addEventListener('change',syncCodexStart);
el('codexRefresh').addEventListener('click',refreshCodex);
el('codexStart').addEventListener('click',async()=>{
 if(!operatorToken || !el('codexConsent').checked)return;
 if(!window.confirm('¿Autorizas ejecutar Codex CLI en modo lectura sobre la copia temporal del catálogo Live Forge? No se aplicarán cambios.'))return;
 codexBusy=true;syncCodexStart();
 try{
  const r=await fetch('/api/codex/start',{method:'POST',credentials:'omit',
   headers:{'Content-Type':'application/json','Authorization':'Bearer '+operatorToken},
   body:JSON.stringify({topic:el('codexTopic').value,activity:el('codexActivity').value,consent:true})});
  const data=await r.json();
  if(!r.ok){el('codexStatus').textContent=`Análisis no iniciado: ${data.error||'rechazado'}`;return;}
  el('codexStatus').textContent=`Trabajo solicitado: ${data.job_id}. Consultando progreso...`;
 }catch(_){el('codexStatus').textContent='No se pudo iniciar Codex.';}
 finally{codexBusy=false;syncCodexStart();refreshCodex();}
});
initialize();
