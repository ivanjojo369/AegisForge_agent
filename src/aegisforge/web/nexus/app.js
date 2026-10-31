/* AegisForge Nexus Colossus unified console v2.
 * No dependencies, no analytics, no persistent credentials, no arbitrary tool execution.
 * Optional fixed Docker fixture with individually supervised effects.
 * All text from model or imported files is inserted using textContent, never HTML.
 */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const state = { mode: 'offline', connected: false, token: '', planId: '',
    data: null, events: [], busy: false, label: '', supplementalPlan: null,
    workspace: 'nexus', proposals: [], nextProposalId: 1, sessionEpoch: 0 };
  const MAX_BYTES = 1024 * 1024;
  const MAX_PROPOSALS = 8;
  let demoEnabled = false;
  let demoPoll = null;
  let demoPendingId = null;
  let demoPendingConfirmation = null;
  let demoLastResult = "";
  let repoInspectionAvailable = false;
  let nebiusProposalEnabled = false;
  let nebiusProposalSpent = false;
  let repoRevisionOffer = null;
  let repoRevisionSpent = false;
  let nebiusCatalogChecked = false;
  let repoInventory = [];
  let repoReadEpoch = 0;
  let repoReviewedSha = '';
  let repoReviewOffer = null;
  let gridLastResult = null;
  let gridLastMultiday = null;
  let gridCheckpointEnabled = false;
  const workspaceNames = {nexus:'NEXUS ENGINEERING',grid:'AEGISGRID',live:'LIVE FORGE'};
  const workspaceInfo = {
    nexus: ['MODO NEXUS','Reparación verificable de software','Inspecciona un archivo autorizado localmente; las modificaciones siguen bloqueadas.','PLANTILLA LOCAL'],
    grid: ['MODO AEGISGRID','Ingeniería para resiliencia energética','Describe una instalación y las variables necesarias para comparar estrategias en un evaluador real.','ESCENARIO HIPOTÉTICO'],
    live: ['MODO LIVE FORGE','Experiencia participativa de programación','Simula propuestas y votos locales antes de conectarlos a una integración real con Twitch.','SIN CONEXIÓN A TWITCH']
  };
  const safeText = (v, limit = 220) => String(v == null ? '' : v).slice(0, limit);
  const count = (n) => Number.isInteger(n) && n >= 0 ? n : null;
  const val = (n, fallback = '—') => n === null || n === undefined ? fallback : String(n);
  const node = (tag, className = '', textValue = '') => {
    const e = document.createElement(tag);
    if (className) e.className = className;
    e.textContent = textValue;
    return e;
  };
  const boolStat = (v) => count(v) ?? '—';
  const cleanState = (o) => o && typeof o === 'object' && !Array.isArray(o) ? o : {};

  function navigation() {
    for (const el of document.querySelectorAll('[data-nav]')) {
      el.classList.toggle('active', el.dataset.nav === (location.hash.replace('#', '') || 'overview'));
    }
  }
  function setMsg(message) { $('api-message').textContent = message; }
  function changeBusy(busy) { state.busy = busy; updateControls(); }
  function updateControls() {
    $('connect-btn').disabled = state.busy || state.mode !== 'live';
    $('prepare-btn').disabled = state.busy || !state.connected;
    $('preview-btn').disabled = state.busy || !state.connected || !state.planId;
    $('finalize-btn').disabled = state.busy || !state.connected || !state.planId;
  }
  function labelFor(status) {
    const mapping = { verified: 'Verificado', failed: 'Fallido', inconclusive: 'Inconcluso',
      not_run: 'No ejecutado', awaiting_evidence: 'Sin evidencia', completed: 'Completado' };
    return mapping[status] || (status ? safeText(status, 30) : 'Sin resultado');
  }
  function linkedPlanMatches(summary, plan) {
    if (!summary || !plan) return false;
    if (!Array.isArray(plan.steps) || plan.steps.length < 1 || plan.steps.length > 20) return false;
    if (plan.type !== 'aegisforge_nexus_plan_review') return false;
    // Require a positive link to the SAME run; a model name or step count alone is not enough.
    if (typeof plan.request_id !== 'string' || !plan.request_id ||
        typeof summary.request_id !== 'string' || summary.request_id !== plan.request_id) return false;
    if (typeof plan.plan_sha256 !== 'string' || typeof summary.plan_sha256 !== 'string' ||
        summary.plan_sha256 !== plan.plan_sha256) return false;
    if (summary.model && plan.model && summary.model !== plan.model) return false;
    const expected = count(summary.plan_step_count) ?? count(summary.step_count);
    return expected === null || expected === plan.steps.length;
  }

  function displayData(data, label, events) {
    state.data = cleanState(data);
    state.label = label;
    if (Array.isArray(events)) state.events = events;
    const d = state.data;
    const originalSteps = Array.isArray(d.plan_steps) ? d.plan_steps :
                         Array.isArray(d.plan?.steps) ? d.plan.steps :
                         Array.isArray(d.steps) ? d.steps : [];
    const supplemental = !originalSteps.length && linkedPlanMatches(d, state.supplementalPlan);
    const steps = supplemental ? state.supplementalPlan.steps : originalSteps;
    const stepCount = count(d.plan_step_count) ?? count(d.plan_steps) ??
      count(d.step_count) ?? (steps.length || null);
    const invocations = count(d.tools_invoked);
    const checksRun = count(d.independent_checks_run);
    const checksPassed = count(d.independent_checks_passed);
    const outcome = d.verification_status || d.verdict || d.status || '';
    $('source-summary').textContent = label;
    $('source-badge').textContent = label.startsWith('API') ? 'API LOCAL' : 'ARCHIVO LOCAL';
    $('m-verdict').textContent = labelFor(outcome);
    $('m-verdict-caption').textContent = 'Estado reportado, no auditado por la interfaz';
    $('m-steps').textContent = val(stepCount);
    $('m-tools').textContent = val(invocations);
    $('m-checks').textContent = checksRun === null ? '—' : `${val(checksPassed, '?')} / ${checksRun}`;
    $('execution-tag').textContent = safeText(d.execution_status || d.status || '—', 28);
    $('plan-label').textContent = steps.length ?
      `${steps.length} pasos${supplemental ? ' · externo' : ''}` : 'Sin desglose';
    $('model-label').textContent = safeText(d.model || d.planner_model || d.provider || '—', 50);
    $('sha-label').textContent = safeText(d.audit_sha256 || d.plan_sha256 || '—', 80);
    $('sha-label').title = $('sha-label').textContent;
    $('events-label').textContent = val(count(d.audit_events) ?? (state.events.length || null));

    const card = $('verdict-card');
    card.classList.remove('success', 'warn', 'fail');
    if (outcome === 'verified') card.classList.add('success');
    if (outcome === 'failed') card.classList.add('fail');
    if (outcome === 'inconclusive' || outcome === 'awaiting_evidence') card.classList.add('warn');
    $('verdict-icon').textContent = outcome === 'verified' ? '✓' : outcome === 'failed' ? '×' : '?';
    $('verdict-title').textContent = labelFor(outcome);
    $('verdict-message').textContent = outcome === 'verified'
      ? 'El resultado cargado reporta verificaciones satisfactorias; comprueba su JSONL para validar la integridad.'
      : outcome === 'inconclusive' ? 'La ejecución no cuenta con verificaciones independientes suficientes.'
      : outcome === 'failed' ? 'El resultado reporta una comprobación fallida.'
      : 'No se ha recibido un veredicto final de verificación.';
    renderSteps(steps, supplemental);
    renderEvents();
  }

  function renderSteps(steps, supplemental = false) {
    const list = $('plan-steps'); list.replaceChildren();
    if (!steps.length) { list.appendChild(node('div','empty-block',
      'El resumen solo incluye la cantidad de pasos. Para verlos importa un plan adicional de la misma ejecución.')); return; }
    if (supplemental) {
      list.appendChild(node('p', 'fine-note',
        'Desglose adicional reconstruido del registro de consola. Se comprobaron los identificadores del run, NO su autenticidad criptográfica.'));
    }
    for (const [idx, item] of steps.slice(0, 20).entries()) {
      const s = cleanState(item), kind = ['reason','tool','verify'].includes(s.kind) ? s.kind : 'reason';
      const row = node('div', 'step-row'); row.dataset.kind = kind;
      row.appendChild(node('div', 'step-number', safeText(s.id || `s${idx+1}`, 8).replace(/^s/, '')));
      const body = node('div','step-body'); const top = node('div','step-top');
      top.appendChild(node('strong', '', safeText(s.title || `Paso ${idx+1}`, 170)));
      top.appendChild(node('span', 'step-kind', kind === 'tool' ? 'Herramienta' : kind === 'verify' ? 'Verificación' : 'Análisis'));
      body.appendChild(top);
      if (s.description) body.appendChild(node('p', '', safeText(s.description, 500)));
      if (s.tool) body.appendChild(node('span','step-tool', safeText(s.tool, 100)));
      row.appendChild(body);list.appendChild(row);
    }
  }

  function renderEvents() {
    const container = $('evidence-items'); container.replaceChildren();
    if (!state.events.length) {
      container.appendChild(node('div','empty-inline','Sin eventos JSONL cargados. Puedes seleccionarlos desde tu carpeta evidence/nexus.'));
      return;
    }
    for (const [idx, item] of state.events.slice(0, 40).entries()) {
      const event = cleanState(item), row = node('div','event-row'), left = node('div');
      const prefix = node('span','event-num',`#${val(count(event.sequence), String(idx+1))}`);
      const title = node('strong','',safeText(event.stage || 'Evento', 60));
      left.appendChild(prefix); left.appendChild(title);
      const detail = node('p','',safeText(event.at_utc || 'Registro importado (integridad no validada aquí)', 110));
      left.appendChild(detail);row.appendChild(left);
      row.appendChild(node('span','event-status',safeText(event.status || '—', 45)));
      container.appendChild(row);
    }
  }
  async function loadFiles(files) {
    if (!files || !files.length) return;
    // Stage one import batch and render once: don't silently mix different runs.
    const selection = { summary: null, audit: null, plan: null };
    try {
      for (const file of files) {
        if (!/\.jsonl?$/i.test(file.name) || file.size > MAX_BYTES) {
          throw new Error(`Solo .json y .jsonl de hasta 1 MB: ${safeText(file.name, 60)}`);
        }
        const raw = await file.text();
        if (file.name.toLowerCase().endsWith('.jsonl')) {
          if (selection.audit) throw new Error('Selecciona un solo archivo JSONL por ejecución.');
          const lines = raw.split(/\r?\n/).filter(Boolean);
          if (!lines.length || lines.length > 50) throw new Error('Número de eventos no válido.');
          selection.audit = lines.map(x => {
            const value = JSON.parse(x);
            if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Evento JSONL inválido.');
            return value;
          });
        } else {
          const parsed = JSON.parse(raw);
          if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Se esperaba un objeto JSON.');
          if (parsed.type === 'aegisforge_nexus_plan_review') {
            if (selection.plan) throw new Error('Selecciona un solo desglose adicional.');
            selection.plan = parsed;
          } else {
            if (selection.summary) throw new Error('Selecciona un solo resumen por ejecución.');
            selection.summary = { data: parsed, label: `Archivo local: ${safeText(file.name, 80)}` };
          }
        }
      }

      // JSON summary, if present, starts a fresh run. Keep existing summary only
      // for a follow-on JSONL or plan import; never retain unrelated previous events.
      let nextData = selection.summary ? selection.summary.data : state.data;
      let nextLabel = selection.summary ? selection.summary.label : state.label;
      let nextEvents = selection.summary ? [] : state.events;
      let nextPlan = selection.summary ? null : state.supplementalPlan;

      if (selection.audit) {
        const last = selection.audit[selection.audit.length - 1];
        if (nextData && !nextData.audit_sha256 && (selection.summary || state.data)) {
          throw new Error('No hay huella audit_sha256 para vincular esta auditoría al resumen. Importa solo el JSONL o incluye un resumen vinculado.');
        }
        if (nextData?.audit_sha256 && last.sha256 !== nextData.audit_sha256) {
          throw new Error('La huella final JSONL no coincide con el resumen cargado. No se combinaron.');
        }
        nextEvents = selection.audit;
        if (!nextData) {
          nextData = {
            verification_status: last.stage === 'verification' ? last.status : 'not_run',
            plan_step_count: count(nextEvents[0]?.summary?.steps),
            tools_invoked: count(nextEvents[1]?.summary?.tools_invoked),
            independent_checks_run: count(last.summary?.independent_checks_run),
            independent_checks_passed: count(last.summary?.independent_checks_passed),
            audit_sha256: last.sha256, audit_events: nextEvents.length,
          };
          nextLabel = 'Archivo local (solo JSONL)';
        }
      }

      if (selection.plan) {
        if (!nextData) {
          throw new Error('Carga primero el resumen JSON de la ejecución; el plan adicional no acredita resultados.');
        }
        if (!linkedPlanMatches(nextData, selection.plan)) {
          throw new Error('El desglose de pasos NO coincide con request_id, plan_sha256, modelo o cantidad del resumen.');
        }
        nextPlan = selection.plan;
      }
      if (!nextData) throw new Error('No se encontró un resumen para visualizar.');

      state.supplementalPlan = nextPlan;
      displayData(nextData, nextLabel || 'Archivo local', nextEvents);
      setMsg(`${files.length} archivo(s) importado(s) sin enviarlos a Nebius. La vista NO verifica la autenticidad de los archivos.`);
      location.hash = '#overview';
    } catch (err) {
      setMsg(`Importación rechazada: ${safeText(err.message, 180)}`);
    }
  }

  async function request(path, {method='GET', body=null, textMode=false} = {}) {
    if (!state.token) throw new Error('Token de Nexus no configurado.');
    const headers = { 'Authorization': `Bearer ${state.token}`, 'Accept':'application/json' };
    if (body !== null) headers['Content-Type'] = 'application/json';
    const response = await fetch(path, { method, headers, body: body === null ? undefined : JSON.stringify(body), cache:'no-store', credentials:'omit', redirect:'error' });
    if (!response.ok) {
      const descriptions = {401:'Token rechazado.',403:'Acción prohibida por la API.',404:'Plan no encontrado o expirado.',409:'El plan ya fue utilizado o no está listo.',429:'Capacidad de planes excedida.',502:'Falló la planificación; revisa el terminal local.'};
      throw new Error(descriptions[response.status] || `HTTP ${response.status}`);
    }
    return textMode ? response.text() : response.json();
  }
  async function connect() {
    if (state.mode !== 'live') { setMsg('Arranca el servidor con --live para habilitar el API de planificación.'); return; }
    state.token = $('api-token').value.trim();
    if (state.token.length < 32) { setMsg('El token local debe tener al menos 32 caracteres.'); state.token = ''; return; }
    changeBusy(true);
    const epoch = ++state.sessionEpoch;
    try { const capabilities = await request('/nexus/capabilities');
      if (epoch !== state.sessionEpoch) return;
      if (capabilities.tool_execution_over_http !== false) throw new Error('Configuración de seguridad inesperada: herramientas HTTP habilitadas.');
      state.connected = true; state.planId = ''; $('api-badge').textContent = 'Conectado';
      $('api-token').value = ''; setMsg('Conectado. Solo planifica y previsualiza; la API no ejecuta herramientas.');
    } catch(err) { if(epoch === state.sessionEpoch) { state.connected = false; state.token = ''; $('api-badge').textContent = 'Desconectado'; setMsg(safeText(err.message,140)); } }
    finally { if(epoch === state.sessionEpoch) changeBusy(false); }
  }
  function disconnect() { state.sessionEpoch++; state.busy=false; state.connected=false; state.token=''; state.planId=''; $('api-token').value=''; $('api-badge').textContent='Desconectado'; setMsg('Token descartado de la memoria de esta página.'); updateControls(); }
  async function prepare() {
    if (!state.connected) return;
    const task = $('task-input').value.trim();
    if (!task || task.length > 2000) { setMsg('Escribe una tarea de 1 a 2000 caracteres.'); return; }
    if (!window.confirm('La preparación enviará UNA petición real a Nemotron mediante Nebius y puede consumir créditos. ¿Autorizar esta petición?')) return;
    changeBusy(true);state.planId=''; const epoch=state.sessionEpoch;
    try { const d = await request('/nexus/prepare', { method:'POST', body:{task,constraints:['No utilices herramientas ni ejecutes código','El plan debe terminar con una verificación independiente']}});
      if(epoch !== state.sessionEpoch || !state.connected) return;
      state.planId = d.plan_id;
      displayData({ model:d.plan?.model, plan:d.plan, plan_step_count:d.plan?.steps?.length,
        verification_status:'not_run',tools_invoked:0,independent_checks_run:0 }, 'API local: plan preparado', []);
      setMsg('Nemotron generó un plan. Revisa sus pasos antes de previsualizar. Ninguna herramienta fue ejecutada.');
      location.hash='#plan';
    } catch(err) { if(epoch === state.sessionEpoch) setMsg(`Preparación fallida: ${safeText(err.message,150)} No hay reintentos automáticos.`); }
    finally {if(epoch === state.sessionEpoch) changeBusy(false);}
  }
  async function preview() {
    if (!state.planId || !state.connected) return;changeBusy(true); const epoch=state.sessionEpoch;
    try { const d = await request(`/nexus/plans/${encodeURIComponent(state.planId)}/preview`, {method:'POST',body:{}});
      if(epoch !== state.sessionEpoch || !state.connected) return;
      if (state.data) { const merged = {...state.data, preview_status:d.status};displayData(merged,'API local: previsualización',state.events); }
      setMsg(`Vista previa: ${safeText(d.status,35)}; ${boolStat(d.tools_invoked)} herramientas ejecutadas.`);
    } catch(err) {if(epoch === state.sessionEpoch) setMsg(safeText(err.message,160));}finally{if(epoch === state.sessionEpoch) changeBusy(false);}
  }
  async function finalize() {
    if (!state.planId || !state.connected) return;
    if (!window.confirm('Esto cerrará el plan SIN ejecutar herramientas. Su estado de verificación puede ser inconcluso. ¿Continuar?')) return;
    changeBusy(true); const epoch=state.sessionEpoch;
    try {const d = await request(`/nexus/plans/${encodeURIComponent(state.planId)}/run`, {method:'POST',body:{execute_tools:false}});
      if(epoch !== state.sessionEpoch || !state.connected) return;
      const plan = state.data?.plan; displayData({...d, plan},'API local: ejecución sin herramientas', []);
      const raw = await request(`/nexus/plans/${encodeURIComponent(state.planId)}/evidence`,{textMode:true});
      if(epoch !== state.sessionEpoch || !state.connected) return;
      const events = raw.split(/\r?\n/).filter(Boolean).map(x => JSON.parse(x));
      displayData({...d,plan},'API local: resultado + auditoría',events);
      setMsg('Finalizado. Herramientas HTTP desactivadas; la auditoría se obtuvo de la API.');
      state.planId=''; location.hash='#evidence';
    }catch(err){if(epoch === state.sessionEpoch){setMsg(safeText(err.message,180));state.planId='';}}finally{if(epoch === state.sessionEpoch)changeBusy(false);}
  }

  // Domain panels create SPECIFICATIONS ONLY. They never invoke tools, Nebius, or Twitch.
  function selectWorkspace(key) {
    if (!workspaceInfo[key]) return;
    state.workspace = key;
    for (const button of document.querySelectorAll('[data-mode]')) {
      const active = button.dataset.mode === key;
      button.classList.toggle('selected', active);
      button.setAttribute('aria-pressed', String(active));
    }
    for (const name of Object.keys(workspaceInfo)) $('workspace-' + name).hidden = name !== key;
    const [kicker,title,description,availability] = workspaceInfo[key];
    $('workspace-kicker').textContent = kicker;
    $('workspace-title').textContent = title;
    $('workspace-description').textContent = description;
    $('workspace-availability').textContent = availability;
    $('selected-mode-label').textContent = workspaceNames[key];
    $('workspace-message').textContent = 'La selección es local; todavía no se ha enviado ninguna petición.';
  }

  function placeTask(text, origin) {
    if (text.length > 2000) {
      $('workspace-message').textContent = 'La descripción supera los 2000 caracteres permitidos.';
      return;
    }
    $('task-input').value = text;
    $('workspace-message').textContent = `${origin}: tarea preparada sin ejecutar acciones ni realizar consultas.`;
    setMsg('Tarea cargada en el planificador. Revísala; la petición a Nebius requiere conexión y confirmación explícita.');
    location.hash = '#neb';
  }

  function makeNexusTask() {
    const goal = $('repo-goal').value.trim();
    if (!goal || goal.length > 900) { $('workspace-message').textContent = 'Describe un objetivo de 1 a 900 caracteres.'; return; }
    const kind = $('repo-kind').selectedOptions[0].textContent;
    placeTask(`MODO NEXUS ENGINEERING. Proyecto: ${kind}. Objetivo: ${goal}\nAnaliza únicamente el contexto que se te proporcione; no supongas acceso al repositorio. Formula un plan generalista para comprender dependencias, proponer un diff mínimo y definir pruebas reproducibles. Identifica riesgos, permisos necesarios y criterios de verificación. Sin ejecutar herramientas ni modificar archivos.`, 'Nexus');
  }

  function numericInput(id, low, high) {
    const raw = $(id).value.trim();
    if (!raw || !/^[-+]?(?:\d+\.?\d*|\.\d+)$/.test(raw)) throw new Error('Completa los valores numéricos del escenario.');
    const value = Number(raw);
    if (!Number.isFinite(value) || value < low || value > high) throw new Error('Valor fuera del rango admitido: ' + id.replace('energy-', ''));
    return value;
  }

  function gridInputs() {
    const demand=numericInput('energy-demand',0.1,1000000);
    return {
      site:$('energy-context').value,
      currency:$('energy-currency').value,
      demand_kwh:demand,
      critical_kwh:numericInput('energy-critical',0,demand),
      outage_hours:numericInput('energy-outage',0,24),
      solar_kwh:numericInput('energy-solar',0,1000000),
      battery_kwh:numericInput('energy-battery',0,1000000),
      tariff:numericInput('energy-tariff',0,10000)
    };
  }

  function makeGridTask() {
    try {
      const {site,currency,demand_kwh:demand,critical_kwh:critical,outage_hours:outage,
             solar_kwh:solar,battery_kwh:battery,tariff}=gridInputs();
      placeTask(`MODO AEGISGRID. ESCENARIO HIPOTÉTICO (NO SON MEDICIONES REALES). Instalación: ${site}. Demanda estimada: ${demand} kWh/día; tarifa: ${tariff} ${currency}/kWh; demanda crítica estimada: ${critical} kWh/día; cortes: ${outage} horas/día; solar: ${solar} kWh/día; batería utilizable: ${battery} kWh.\nPropón una metodología verificable para comparar una estrategia de referencia con una optimización de costo y continuidad. No inventes ahorros ni resultados: explica datos horarios faltantes, restricciones de batería, cargas críticas, supuestos, métricas y verificadores independientes. No controles equipos reales ni ejecutes herramientas.`, 'AegisGrid');
    } catch (error) { $('workspace-message').textContent = safeText(error.message, 150); }
  }

  async function simulateGrid() {
    if (gridCheckpointEnabled && !window.confirm(
      'La simulación guardará metadatos de la misión en el directorio local configurado para Mission Control. ¿Continuar?'
    )) return;
    const button=$('grid-simulate');
    const panel=$('grid-simulation-result');
    const details=$('grid-simulation-details');
    button.disabled=true;
    gridLastResult=null;
    $('grid-export-evidence').disabled=true;
    panel.hidden=true;
    $('workspace-message').textContent='Calculando escenario hipotético de 24 horas...';
    try {
      const scenario=gridInputs();
      const response=await fetch('/aegisgrid/simulate',{
        method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},
        body:JSON.stringify(scenario),cache:'no-store',credentials:'omit',redirect:'error'
      });
      if (!response.ok) throw new Error(`El servidor rechazó la simulación (HTTP ${response.status}).`);
      const data=await response.json();
      if (data.schema!=='aegisforge.aegisgrid.simulation.v1' || data.verification?.passed!==true)
        throw new Error('La simulación no superó sus comprobaciones matemáticas internas.');
      if (data.colossus?.schema!=='aegisforge.aegisgrid.colossus_integration.v1' ||
          !data.colossus.mission || !data.colossus.laboratory || !data.colossus.proof_graph)
        throw new Error('Falta la integración registrada con Laboratory, ProofGraph y Mission Control.');
      const base=data.reference.totals;
      const plan=data.solar_battery.totals;
      const n=v=>Number(v).toFixed(2);
      details.textContent=[
        'Escenario hipotético: 24 h · 48 intervalos de 30 min (NO mediciones reales)',
        `Solo red: ${n(base.critical_unserved_kwh)} kWh críticos sin suministro; ${n(base.grid_cost)} ${scenario.currency} en energía de red`,
        `Solar+batería: ${n(plan.critical_unserved_kwh)} kWh críticos sin suministro; ${n(plan.grid_cost)} ${scenario.currency} en energía de red`,
        `Demanda total sin suministro: ${n(base.total_unserved_kwh)} → ${n(plan.total_unserved_kwh)} kWh`,
        `Carga crítica atendida: ${n(base.critical_served_percent)}% → ${n(plan.critical_served_percent)}%`,
        `Batería al final: ${n(plan.final_battery_soc_kwh)} kWh de ${n(scenario.battery_kwh)} kWh utilizables`,
        'Balance matemático interno: aprobado. Verificación externa: PENDIENTE.',
        'Supuestos: carga uniforme, sol idealizado 06–18 h, corte desde las 18 h, batería inicial al 50%.',
        'No hay control de infraestructura, optimización demostrada ni cálculo de inversión o mantenimiento.',
        `SHA-256 de consistencia del resultado: ${data.result_sha256}`,
        '',
        'COLOSSUS · LABORATORY / PROOFGRAPH / MISSION REGISTRY',
        `Misión: ${safeText(data.colossus.mission.mission_id, 95)} · Estado: ${safeText(data.colossus.mission.status, 40)}`,
        `Laboratory: ${safeText(data.colossus.laboratory.status, 30)} · Relectura independiente de balances: ${data.colossus.laboratory.replay.passed ? 'APROBADA' : 'FALLIDA'}`,
        `Comprobaciones de relectura: ${Object.values(data.colossus.laboratory.replay.checks).filter(Boolean).length} de ${Object.keys(data.colossus.laboratory.replay.checks).length}`,
        `ProofGraph: ${data.colossus.proof_graph.node_count} nodos · ${data.colossus.proof_graph.edge_count} relaciones`,
        `Registro: ${data.colossus.storage.kind === 'checkpoint' ? 'checkpoint local, generación '+data.colossus.storage.generation : 'memoria de este servidor (no persistente)'}`,
        `Archivo de fuentes Laboratory/ProofGraph: ${data.colossus.storage.external_monitor_sources_archived ? 'GUARDADO (metadatos históricos)' : 'no guardado'}`,
        'Estado de misión no equivale a validación física ni autenticidad externa.',
        'El dashboard independiente de Mission Control solo leerá un checkpoint cuando se inicie con esa fuente.',
        `Scorecard SHA-256: ${safeText(data.colossus.laboratory.scorecard_sha256, 64)}`
      ].join('\n');
      gridLastResult=data;
      $('grid-export-evidence').disabled=false;
      panel.hidden=false;
      $('workspace-message').textContent='Simulación registrada en los contratos Colossus; validación del mundo real pendiente.';
    } catch (error) {
      $('workspace-message').textContent=`Simulación no completada: ${safeText(error.message,180)}`;
    } finally { button.disabled=false; }
  }

  async function simulateGridMultiday() {
    if (gridCheckpointEnabled && !window.confirm(
      'Se registrará una misión y se archivarán resúmenes de Laboratory y ProofGraph. ¿Continuar?'
    )) return;
    const button=$('grid-multiday');
    const panel=$('grid-multiday-result');
    button.disabled=true;
    gridLastMultiday=null;
    $('grid-multiday-export').disabled=true;
    panel.hidden=true;
    $('workspace-message').textContent='Comparando estrategias energéticas durante varios días...';
    try {
      const request={scenario:gridInputs(),days:Number($('grid-days').value),
        weather:$('grid-weather').value,outage_schedule:$('grid-outages').value};
      const response=await fetch('/aegisgrid/multiday',{
        method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},
        body:JSON.stringify(request),cache:'no-store',credentials:'omit',redirect:'error'
      });
      if(!response.ok)throw new Error(`No se pudo calcular la comparación (HTTP ${response.status}).`);
      const data=await response.json();
      if(data.schema!=='aegisforge.aegisgrid.multiday.v1' || data.verification?.passed!==true ||
         data.colossus?.laboratory?.replay?.passed!==true || !data.colossus?.mission)
        throw new Error('No se recibieron verificaciones de balances y misión de Colossus.');
      const label={grid_only:'Solo red',solar_balanced:'Solar equilibrada',
        solar_critical_reserve:'Reserva crítica',solar_grid_recharge:'Recarga desde red'};
      const n=v=>Number(v).toFixed(2);
      const lines=[
        `Horizonte: ${request.days} día(s), ${request.days*48} franjas de 30 minutos por estrategia`,
        `Sol hipotético: ${request.weather} · cortes: ${request.outage_schedule} (NO pronóstico)`,
        '', 'ESTRATEGIAS / ENERGÍA CRÍTICA NO SUMINISTRADA / TOTAL NO SUMINISTRADA / COSTO DE RED',
      ];
      for(const [policy,item] of Object.entries(data.strategies)){
        const t=item.totals;
        lines.push(`${label[policy]||policy}: ${n(t.critical_unserved_kwh)} kWh / ${n(t.total_unserved_kwh)} kWh / ${n(t.grid_cost)} ${request.scenario.currency} · batería final ${n(t.final_battery_soc_kwh)} kWh`);
      }
      lines.push('',`Mejor entre políticas ensayadas: ${label[data.comparison.selected_policy]||data.comparison.selected_policy}`,
        'Criterio: primero continuidad crítica, después demanda total atendida, luego costo de red.',
        'No demuestra óptimo global; no considera CAPEX, mantenimiento ni mediciones reales.',
        '', 'ENERGÍA CRÍTICA NO SUMINISTRADA POR DÍA / POLÍTICA SELECCIONADA');
      const winner=data.strategies[data.comparison.selected_policy];
      for(const day of winner.daily)lines.push(`Día ${day.day}: ${n(day.critical_unserved_kwh)} kWh críticos · ${n(day.total_unserved_kwh)} kWh total · batería ${n(day.initial_soc_kwh)} → ${n(day.final_soc_kwh)} kWh`);
      const replay=data.colossus.laboratory.replay;
      lines.push('', 'COLOSSUS · LABORATORY / PROOFGRAPH / MISSION CONTROL',
        `Misión: ${safeText(data.colossus.mission.mission_id,96)} · ${safeText(data.colossus.mission.status,36)}`,
        `Revisión independiente: ${Object.values(replay.checks).filter(Boolean).length} / ${Object.keys(replay.checks).length} comprobaciones`,
        `ProofGraph: ${data.colossus.proof_graph.node_count} nodos · ${data.colossus.proof_graph.edge_count} relaciones`,
        `Archivo de fuentes: ${data.colossus.storage.external_monitor_sources_archived?'GUARDADO':'NO GUARDADO'} · Generación ${data.colossus.storage.generation ?? 'en memoria'}`,
        `SHA-256 resultado: ${data.result_sha256}`,
        'Integridad matemática no equivale a validación física ni a autenticidad externa.');
      $('grid-multiday-details').textContent=lines.join('\n');
      gridLastMultiday=data;
      $('grid-multiday-export').disabled=false;
      panel.hidden=false;
      $('workspace-message').textContent='Comparación energética registrada; revisa sus limitaciones y evidencia.';
    }catch(error){
      $('workspace-message').textContent=`Comparación no completada: ${safeText(error.message,180)}`;
    }finally{button.disabled=false;}
  }

  function exportGridMultiday() {
    if(!gridLastMultiday?.colossus?.mission)return;
    const file=new Blob([JSON.stringify(gridLastMultiday,null,2)],{type:'application/json'});
    const link=document.createElement('a');
    const href=URL.createObjectURL(file);
    link.href=href;
    link.download='aegisgrid-multiday-'+gridLastMultiday.colossus.mission.mission_id+'.json';
    document.body.appendChild(link);link.click();link.remove();
    setTimeout(()=>URL.revokeObjectURL(href),1000);
  }

  function exportGridEvidence() {
    if (!gridLastResult || !gridLastResult.colossus) return;
    const file = new Blob([JSON.stringify(gridLastResult, null, 2)], {type:'application/json'});
    const link = document.createElement('a');
    const href = URL.createObjectURL(file);
    link.href=href;
    link.download='aegisgrid-' + gridLastResult.colossus.mission.mission_id + '.json';
    document.body.appendChild(link);
    link.click();
    link.remove();
    // Revoke shortly after the synchronous user activation has been consumed.
    setTimeout(()=>URL.revokeObjectURL(href), 1000);
  }

  function renderLiveProposals() {
    const list=$('live-proposals'); list.replaceChildren();
    if (!state.proposals.length) {
      list.appendChild(node('div','empty-inline','Sin propuestas. Añade una para simular una votación.'));
      $('live-to-task').disabled = true;
      return;
    }
    $('live-to-task').disabled = false;
    for (const proposal of [...state.proposals].sort((a,b)=>b.votes-a.votes || a.id-b.id)) {
      const row=node('div','proposal-row');
      const body=node('div','proposal-copy');
      body.appendChild(node('strong','',proposal.text));
      body.appendChild(node('small','',`${proposal.votes} voto(s) locales`));
      const vote=node('button','small-btn','Votar +1');vote.type='button';vote.setAttribute('aria-label','Votar por '+proposal.text);
      vote.addEventListener('click',()=>{if (proposal.votes>=999) return;proposal.votes++;renderLiveProposals();});
      row.appendChild(body);row.appendChild(vote);list.appendChild(row);
    }
  }

  function addLiveProposal() {
    const idea=$('live-idea').value.trim().replace(/\s+/g,' ');
    if (!idea || idea.length>120) { $('workspace-message').textContent='Introduce una propuesta entre 1 y 120 caracteres.';return; }
    if (state.proposals.length>=MAX_PROPOSALS) { $('workspace-message').textContent='Máximo ocho propuestas por simulación.';return; }
    if (state.proposals.some(p=>p.text.toLocaleLowerCase()===idea.toLocaleLowerCase())) {
      $('workspace-message').textContent='La propuesta ya existe.';return;
    }
    state.proposals.push({id:state.nextProposalId++,text:idea,votes:0});
    $('live-idea').value='';
    $('workspace-message').textContent='Propuesta añadida localmente. Todavía no se ha interactuado con Twitch.';
    renderLiveProposals();
  }

  function makeLiveTask() {
    const ranked=[...state.proposals].sort((a,b)=>b.votes-a.votes || a.id-b.id);
    if (!ranked.length) return;
    const winner=ranked[0];
    placeTask(`MODO LIVE FORGE. SIMULACIÓN LOCAL SIN CONEXIÓN A TWITCH. La idea más votada (${winner.votes} votos en esta pestaña) fue: "${winner.text}".\nDiseña una función interactiva de streaming que pueda integrarse en el futuro mediante Twitch chat, EventSub u overlay. Plantea el flujo espectador → propuesta → revisión explícita del streamer → actualización visible. Describe cómo demostrar el desarrollo con Codex, los límites de seguridad y las pruebas. No asumas eventos reales ni ejecutes herramientas.`, 'Live Forge');
  }

  function resetEvidence() {
    state.data=null;state.events=[];state.supplementalPlan=null;state.label='';state.planId='';updateControls();
    $('source-summary').textContent='Todavía no se ha cargado un resultado.';
    $('source-badge').textContent='SIN DATOS';
    for (const id of ['m-verdict','m-steps','m-tools','m-checks','execution-tag','model-label','sha-label','events-label']) $(id).textContent='—';
    $('m-verdict-caption').textContent='No verificado por la interfaz';
    $('plan-label').textContent='Sin plan';
    $('plan-steps').replaceChildren(node('div','empty-block','Sin pasos cargados.'));
    $('evidence-items').replaceChildren(node('div','empty-inline','Sin eventos de auditoría cargados.'));
    $('verdict-card').classList.remove('success','warn','fail');
    $('verdict-icon').textContent='?';$('verdict-title').textContent='Sin resultado';
    $('verdict-message').textContent='Los resultados importados no se autentican automáticamente.';
    $('sha-label').removeAttribute('title');
    setMsg('Datos y plan pendiente descartados de esta pestaña. Los archivos originales no se modificaron.');
  }

  async function demoRequest(path, body = undefined) {
    const token = $('demo-token').value.trim();
    if (token.length < 32) throw new Error('Ingresa primero el token local de demostración.');
    const headers = {'Authorization': `Bearer ${token}`, 'Accept': 'application/json'};
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const response = await fetch(path, {method: body === undefined ? 'GET' : 'POST',
      headers, body: body === undefined ? undefined : JSON.stringify(body),
      credentials: 'omit', cache: 'no-store', redirect: 'error'});
    if (!response.ok) {
      let code = '';
      if (path === '/demo/repo/model-proposal' || path === '/demo/repo/model-status') {
        try {
          const payload = await response.json();
          const detail = payload.detail || payload;
          if (detail && typeof detail === 'object' && typeof detail.error_code === 'string' &&
              /^[a-z0-9_]{4,48}$/.test(detail.error_code)) code = detail.error_code;
        } catch (_ignored) {}
      }
      throw new Error(`El servidor rechazó la operación (HTTP ${response.status})${code ? ' · diagnóstico: '+code : ''}.`);
    }
    return response.json();
  }

  function showDemo(data) {
    $('demo-message').textContent = safeText(data.message || data.status, 240);
    const pending = data.pending;
    $('demo-review').hidden = !pending;
    $('demo-start').disabled = ['running','awaiting_review'].includes(data.status);
    if (pending) {
      $('demo-review-title').textContent = `Revisión necesaria: ${safeText(pending.label, 90)}`;
      $('demo-effect').textContent = safeText(pending.effect, 240);
      $('demo-patch').textContent = safeText(pending.patch, 220);
      $('demo-fingerprint').textContent = safeText(pending.fingerprint, 80);
      if (demoPendingId !== pending.request_id) {
        demoPendingId = pending.request_id;
        demoPendingConfirmation = pending.confirmation;
      }
    } else {
      demoPendingId = null;
      demoPendingConfirmation = null;
    }
    const revisionPossible = data.status === 'completed' && data.source_snapshot === true &&
      data.result?.target_path === 'scripts/build_scorecard.py' &&
      data.result?.baseline === 'sandbox_failed' &&
      data.result?.candidate === 'sandbox_failed' &&
      data.result?.verified_repair === false && data.result?.original_untouched === true;
    $('repo-revision-feedback').disabled = !revisionPossible;
    if (data.result) {
      const content = JSON.stringify({ ...data.result, round_history: Array.isArray(data.round_history) ? data.round_history : [] }, null, 2);
      $('demo-result').hidden = false;
      if (data.source_snapshot === true) {
        $('repo-run-message').textContent = data.status === 'completed' ?
          'Comparación Docker antes/después finalizada sobre copia de código real; autenticidad y cobertura limitadas.' :
          `Estado de reparación: ${safeText(data.status, 40)}`;
      }
      $('demo-result-text').textContent = safeText(content, 3000);
      if (content !== demoLastResult) {
        demoLastResult = content;
        $('demo-result').scrollIntoView({behavior:'smooth',block:'nearest'});
      }
    }
    if (!['running','awaiting_review'].includes(data.status) && demoPoll !== null) {
      clearInterval(demoPoll); demoPoll = null;
    }
  }

  async function refreshDemo() {
    if (!demoEnabled) return;
    try { showDemo(await demoRequest('/demo/repair/status')); }
    catch (err) { $('demo-message').textContent = safeText(err.message, 180); }
  }

  function pollDemo() {
    if (demoPoll !== null) clearInterval(demoPoll);
    demoPoll = setInterval(refreshDemo, 1000);
  }

  async function startDemo() {
    if (!demoEnabled) return;
    if (!window.confirm('Esta demostración iniciará una ejecución real de Docker en tu equipo. Solo usa archivos sintéticos temporales, sin red y sin Nebius. Cada efecto posterior requerirá revisión individual. ¿Continuar?')) return;
    $('demo-start').disabled = true;
    $('demo-result').hidden = true;
    demoLastResult = '';
    try {
      const data = await demoRequest('/demo/repair/start', {confirmation:'INICIAR DEMOSTRACION SINTETICA'});
      showDemo(data); pollDemo();
    } catch (err) { $('demo-message').textContent = safeText(err.message, 180); $('demo-start').disabled = false; }
  }

  async function decideDemo(decision) {
    if (!demoPendingId) return;
    const currentId = demoPendingId;
    // The operator must still explicitly click for EACH effect. The exact
    // one-time confirmation is bound to the review shown above. No timer or
    // background callback can authorize actions.
    const code = decision === 'approve' ? demoPendingConfirmation : '';
    if (decision === 'approve' && (!code || !/^AUTORIZAR [a-f0-9]{12}$/.test(code))) {
      $('demo-message').textContent = 'La autorización actual no es válida; actualiza el estado.';return;
    }
    $('demo-approve').disabled = true; $('demo-deny').disabled = true;
    try {
      await demoRequest('/demo/repair/decision', {request_id:currentId,decision,confirmation:code});
      await refreshDemo();
    } catch (err) { $('demo-message').textContent = safeText(err.message, 180); }
    finally { $('demo-approve').disabled = false; $('demo-deny').disabled = false; }
  }

  function clearRepoPreview() {
    repoReadEpoch++;
    $('repo-source-panel').hidden = true;
    $('repo-source-message').textContent = '';
    $('repo-static-analysis').textContent = '';
    $('repo-source-text').value = '';
    repoReviewedSha = '';
    $('repo-regression-source').value = '';
    repoRevisionOffer = null;
    $('repo-propose').disabled = true;
    $('repo-nebius-propose').disabled = true;
    $('repo-import-diff').disabled = true;
    $('repo-import-message').textContent = '';
    repoReviewOffer = null;
    $('repo-run-reviewed').disabled = true;
    $('repo-run-message').textContent = '';
    $('repo-proposal-panel').hidden = true;
    $('repo-proposal-message').textContent = '';
    $('repo-proposal-meta').textContent = '';
    $('repo-proposal-diff').textContent = '';
    $('repo-nebius-message').textContent = nebiusProposalEnabled ? 'Elige un archivo permitido y revisa su contenido antes de enviarlo.' : 'Nebius desactivado.';
  }

  async function readRepoFile() {
    if (!repoInspectionAvailable) return;
    const item = repoInventory.find(x => x.relative_path === $('repo-file-choice').value);
    if (!item || typeof item.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(item.sha256)) {
      $('repo-inspect-message').textContent = 'Ese archivo no tiene una huella verificable en el inventario.';
      return;
    }
    if (item.size_bytes > 10000 || item.size_bytes < 1) {
      $('repo-inspect-message').textContent = 'La vista de código está limitada a archivos de hasta 10 KB.';
      return;
    }
    if (!window.confirm(`¿Leer el contenido local de ${item.relative_path}? No se enviará a Nebius ni se ejecutará.`)) return;
    clearRepoPreview();
    const epoch = repoReadEpoch;
    $('repo-read-file').disabled = true;
    $('repo-inspect-message').textContent = 'Leyendo y analizando el archivo autorizado (solo local)...';
    try {
      const data = await demoRequest('/demo/repo/source', {
        relative_path:item.relative_path, expected_sha256:item.sha256,
        confirmation:'LEER ARCHIVO LOCAL AUTORIZADO'
      });
      if (epoch !== repoReadEpoch || $('repo-file-choice').value !== item.relative_path) return;
      if (data.mode !== 'local_read_only' || data.sha256 !== item.sha256 ||
          data.executed !== false || data.modified !== false || data.nebius_calls !== 0 ||
          typeof data.content !== 'string') throw new Error('Respuesta de lectura inesperada');
      $('repo-source-panel').hidden = false;
      $('repo-source-text').value = data.content;
      repoReviewedSha = data.sha256;
      $('repo-propose').disabled = item.relative_path !== 'scripts/build_scorecard.py';
      // Import/review is available for any inventoried, hash-bound text file.
      // Only the scorecard fixture has a Docker regression execution route.
      $('repo-import-diff').disabled = false;
      $('repo-nebius-propose').disabled = !nebiusProposalEnabled || nebiusProposalSpent || !/\.(py|pyi|js|jsx|ts|tsx|mjs|cjs)$/i.test(item.relative_path) || data.content.length > 8000;
      const a = data.analysis || {};
      $('repo-static-analysis').textContent = a.language === 'python' ?
        `Sintaxis: ${safeText(a.syntax, 25)}${a.syntax_error ? ` (línea ${a.syntax_error.line}: ${safeText(a.syntax_error.message, 100)})` : ''} · Imports: ${(a.imports || []).slice(0, 40).join(', ') || '—'} · Funciones: ${(a.functions || []).slice(0, 40).join(', ') || '—'} · Clases: ${(a.classes || []).slice(0, 40).join(', ') || '—'}` :
        'Vista de texto. Análisis sintáctico automático disponible por ahora solo para Python.';
      $('repo-source-message').textContent = `Contenido local leído: ${safeText(data.relative_path, 120)} · ${data.size_bytes} bytes · SHA-256 comprobado. Sin ejecución.`;
      $('repo-inspect-message').textContent = 'Archivo leído en esta pestaña. No se ha ejecutado ni enviado al modelo.';
    } catch (err) {
      if (epoch === repoReadEpoch) $('repo-inspect-message').textContent = `Lectura rechazada: ${safeText(err.message, 140)}`;
    } finally { $('repo-read-file').disabled = false; }
  }

  async function previewRepoProposal() {
    const item = repoInventory.find(x => x.relative_path === $('repo-file-choice').value);
    if (!item || item.relative_path !== 'scripts/build_scorecard.py' ||
        item.sha256 !== repoReviewedSha || $('repo-source-panel').hidden) return;
    if (!window.confirm('¿Preparar una propuesta LOCAL para el fallo conocido de warnings? Solo genera un diff; no escribe archivos, no ejecuta pruebas y no utiliza Nebius.')) return;
    $('repo-propose').disabled = true;
    $('repo-nebius-propose').disabled = true;
    $('repo-import-diff').disabled = true;
    $('repo-import-message').textContent = '';
    repoReviewOffer = null;
    $('repo-run-reviewed').disabled = true;
    $('repo-proposal-panel').hidden = true;
    const epoch = repoReadEpoch;
    $('repo-inspect-message').textContent = 'Revisando propuesta y diff en el servidor local...';
    try {
      const d = await demoRequest('/demo/repo/proposal', {
        relative_path:item.relative_path, expected_sha256:item.sha256,
        confirmation:'REVISAR PROPUESTA LOCAL SIN EFECTOS'
      });
      if (epoch !== repoReadEpoch || $('repo-file-choice').value !== item.relative_path) return;
      if (d.mode !== 'local_review_only' || d.source !== 'deterministic_offline_rule' ||
          d.base_sha256 !== item.sha256 || d.applied !== false || d.executed !== false ||
          d.nebius_calls !== 0 || typeof d.file?.unified_diff !== 'string')
        throw new Error('El servidor devolvió una propuesta inesperada');
      if (typeof d.review_id !== 'string' || typeof d.patchset_sha256 !== 'string' ||
          !/^[a-f0-9]{64}$/.test(d.patchset_sha256)) throw new Error('Falta vínculo de revisión');
      repoReviewOffer = d.docker_execution_enabled ? {
        review_id:d.review_id, expected_sha256:d.base_sha256,
        patchset_sha256:d.patchset_sha256
      } : null;
      $('repo-run-reviewed').disabled = false;
      $('repo-run-message').textContent = 'Revisión disponible por un máximo de 8 minutos; no ejecutada.';
      $('repo-proposal-panel').hidden = false;
      $('repo-proposal-message').textContent = 'Propuesta local verificada para revisión: no aplicada ni probada en Docker.';
      $('repo-proposal-meta').textContent = `Parche: ${safeText(d.patchset_sha256, 75)} · Origen: regla local, no Nemotron. ${safeText(d.summary, 200)}`;
      $('repo-proposal-diff').textContent = d.file.unified_diff;
      $('repo-inspect-message').textContent = 'Diff disponible para revisión manual. No se modificó el archivo.';
    } catch(err) {
      if (epoch === repoReadEpoch) $('repo-inspect-message').textContent = `Propuesta rechazada: ${safeText(err.message, 140)}`;
    } finally {
      if (epoch === repoReadEpoch) $('repo-propose').disabled = false;
    }
  }

  async function checkNemotronCatalog() {
    if (!nebiusProposalEnabled || nebiusCatalogChecked) return;
    if (!window.confirm('¿Consultar UNA VEZ el catálogo de modelos Nebius? No envía código fuente ni ejecuta inferencia. No comprueba el saldo ni la facturación de la solicitud anterior.')) return;
    nebiusCatalogChecked = true;
    $('repo-nebius-check-model').disabled = true;
    $('repo-nebius-message').textContent = 'Consultando catálogo de modelos (sin inferencia ni código fuente)...';
    try {
      const d = await demoRequest('/demo/repo/model-status');
      $('repo-nebius-message').textContent = d.status === 'ok' ?
        (d.model_available === true ?
          `Clave aceptada para consultar modelos. ${safeText(d.model,90)} figura en el catálogo; todavía no se ha probado la inferencia.` :
          `Catálogo accesible, pero el modelo ${safeText(d.model,90)} no figura para esta clave. No solicites inferencia.`) :
        `No se pudo comprobar el catálogo: ${safeText(d.error_code,65)}. No reintentes consultas de pago.`;
    } catch (err) {
      $('repo-nebius-message').textContent = `Comprobación del catálogo fallida: ${safeText(err.message,120)}`;
    }
  }

  async function prepareRevisionFeedback() {
    // This operation is strictly local: no source is sent to Nebius.
    const item = repoInventory.find(x => x.relative_path === $('repo-file-choice').value);
    if (!item || item.relative_path !== 'scripts/build_scorecard.py' ||
        item.sha256 !== repoReviewedSha || $('repo-source-panel').hidden) {
      $('repo-revision-message').textContent =
        'Primero selecciona y lee scripts/build_scorecard.py; luego vuelve a consultar el estado de Docker.';
      return;
    }
    try {
      const d = await demoRequest('/demo/repo/revision-feedback');
      if (d.mode !== 'review_feedback_only' || d.nebius_calls !== 0 ||
          d.source_sent !== false || d.base_sha256 !== item.sha256 ||
          typeof d.run_id !== 'string' || !d.run_id) throw new Error('Retroalimentación incompatible');
      repoRevisionOffer = d.can_request_revision_model ? {run_id:d.run_id, base_sha256:d.base_sha256} : null;
      $('repo-nebius-goal').value = 'Revisa la corrección anterior: aún falla la regresión de warnings. Corrige también warnings=null y los valores no-lista, conservando warning_count y fallback.';
      $('repo-revision-message').textContent = d.can_request_revision_model ?
        'Evidencia de Docker vinculada. Solicitud preparada SIN contactar a Nebius. Revisa el objetivo y autoriza la nueva consulta si lo deseas.' :
        'Fallo registrado, pero la revisión de Nemotron no está habilitada o ya se utilizó. No hay reintento automático.';
      $('repo-nebius-propose').disabled = !repoRevisionOffer || repoRevisionSpent;
    } catch (err) {
      $('repo-revision-message').textContent = `No se preparó la iteración: ${safeText(err.message, 160)}`;
    }
  }

  async function proposeWithNemotron() {
    const item = repoInventory.find(x => x.relative_path === $('repo-file-choice').value);
    const goal = $('repo-nebius-goal').value.trim();
    const revision = repoRevisionOffer;
    if (!nebiusProposalEnabled || (!revision && nebiusProposalSpent) ||
        (revision && repoRevisionSpent) || !item ||
        $('repo-source-panel').hidden || item.sha256 !== repoReviewedSha) return;
    if (goal.length < 10 || goal.length > 600) {
      $('repo-nebius-message').textContent = 'Describe un objetivo concreto de 10 a 600 caracteres.';return;
    }
    const consentScope = revision ? 'SEGUNDA propuesta basada en una regresión fallida' : 'PRIMERA propuesta';
    if (!window.confirm(`Vas a solicitar una ${consentScope}. Se enviará el contenido COMPLETO del archivo autorizado (${item.relative_path}, máximo 8.000 caracteres) y el objetivo a Nebius. Puede generar un NUEVO cargo. No se aplicarán cambios ni se ejecutarán herramientas. No habrá reintentos automáticos. ¿Continuar?`)) return;
    const confirmation = window.prompt('Para autorizar el envío del código y una consulta que podría generar costos, escribe exactamente: AUTORIZAR UNA CONSULTA NEBIUS Y ENVIO DEL CODIGO');
    if (confirmation !== 'AUTORIZAR UNA CONSULTA NEBIUS Y ENVIO DEL CODIGO') {
      $('repo-nebius-message').textContent = 'No se autorizó el envío a Nebius.';return;
    }
    // A model diff can only be considered for the tightly scoped, independent
    // scorecard Docker regression after server-side patch identity verification.
    repoReviewOffer = null;
    $('repo-run-reviewed').disabled = true;
    $('repo-proposal-panel').hidden = true;
    $('repo-nebius-propose').disabled = true;
    const epoch = repoReadEpoch;
    $('repo-nebius-message').textContent = 'Una llamada a Nebius autorizada; revisando la propuesta sin aplicar cambios...';
    // Treat a network failure as an uncertain billable attempt; do not retry.
    if (revision) repoRevisionSpent = true; else nebiusProposalSpent = true;
    repoRevisionOffer = null;
    try {
      const d = await demoRequest('/demo/repo/model-proposal', {
        relative_path:item.relative_path, expected_sha256:item.sha256, goal,
        confirmation, ...(revision ? {revision_run_id:revision.run_id} : {})
      });
      if (epoch !== repoReadEpoch || item.relative_path !== $('repo-file-choice').value) return;
      if (d.mode !== 'nebius_review_only' || d.source !== 'nvidia_via_nebius' ||
          d.base_sha256 !== item.sha256 || d.applied !== false ||
          d.executed !== false || d.nebius_calls !== 1 ||
          typeof d.docker_execution_enabled !== 'boolean' || !Array.isArray(d.files) || d.files.length > 1) {
        throw new Error('Respuesta del modelo no cumple las restricciones de revisión');
      }
      $('repo-proposal-panel').hidden = false;
      $('repo-proposal-message').textContent = d.files.length ?
        'Propuesta de Nemotron, solo revisión. NO aplicada ni comprobada en Docker.' :
        'Nemotron indicó contexto insuficiente; no propuso cambios.';
      $('repo-proposal-meta').textContent =
        `Modelo: ${safeText(d.model,90)} · ${safeText(d.summary,300)} · Tokens reportados: ${safeText(d.usage?.total_tokens ?? 'no disponible', 20)}. Sin ejecución.`;
      $('repo-proposal-diff').textContent = d.files.length ? safeText(d.files[0].unified_diff,16000) : '(sin diferencias propuestas)';
      const eligible = d.docker_execution_enabled === true &&
        item.relative_path === 'scripts/build_scorecard.py' && d.files.length === 1 &&
        /^[a-f0-9]{64}$/.test(d.patchset_sha256 || '') &&
        typeof d.review_id === 'string' && d.review_id.length >= 15;
      repoReviewOffer = eligible ? {
        review_id:d.review_id, expected_sha256:d.base_sha256,
        patchset_sha256:d.patchset_sha256
      } : null;
      $('repo-run-reviewed').disabled = !eligible;
      $('repo-run-message').textContent = eligible ?
        'Esta propuesta exacta puede ejecutarse sobre copias temporales con autorizaciones Trust. Puede FALLAR la regresión y eso se registrará sin afirmar que corrigió el problema.' :
        'El diff del modelo solo puede revisarse; esta ruta de ejecución se limita al scorecard.';
      $('repo-nebius-message').textContent = revision ?
        'Segunda propuesta recibida para revisión humana. No se harán más inferencias en esta sesión.' :
        'Primera propuesta recibida. Solo se permitirá otra consulta si Docker detecta una reparación fallida y vuelves a autorizarla.';
    } catch(err) {
      if (epoch === repoReadEpoch) $('repo-nebius-message').textContent =
        `Consulta no completada o rechazada: ${safeText(err.message,160)}. No reintentes automáticamente: puede haberse facturado.`;
    }
  }

  // Only remove presentation wrappers added by copying from a chat/code block.
  // The server still requires an exact canonical diff and a hash-bound source.
  function normalizePastedUnifiedDiff(input) {
    let value = String(input || '').replace(/\r\n?/g, '\n').replace(/^\uFEFF/, '').trim();
    if (value.startsWith('```')) {
      const fenced = value.match(/^```(?:diff|patch)?[ \t]*\n([\s\S]*?)\n```$/i);
      if (!fenced) return { error: 'El bloque Markdown está incompleto: copia todo el diff o elimina las líneas ```.' };
      value = fenced[1].trim();
    }
    if (!value) return { error: 'El campo está vacío. Copia el diff completo, empezando por --- a/archivo.' };
    if (value.length + 1 > 12000) return { error: 'El diff supera el límite permitido de 12.000 caracteres.' };
    const lines = value.split('\n');
    if (!/^--- a\/[^\n]+$/.test(lines[0] || '') || !/^\+\+\+ b\/[^\n]+$/.test(lines[1] || '')) {
      return { error: 'Faltan los dos encabezados del diff. Copia desde --- a/ruta hasta la última línea, sin texto explicativo.' };
    }
    const oldPath = lines[0].slice(6);
    const newPath = lines[1].slice(6);
    if (oldPath !== newPath) {
      return { error: 'Los encabezados --- y +++ indican rutas distintas. Solo se permite revisar un archivo existente por diff.' };
    }
    if (!lines.some(line => /^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@/.test(line))) {
      return { error: 'El diff incluye encabezados, pero le falta una sección @@ con los cambios.' };
    }
    return { unifiedDiff: value + '\n', relativePath: oldPath };
  }

  async function importPreviouslyReviewedDiff() {
    const item = repoInventory.find(x => x.relative_path === $('repo-file-choice').value);
    if (!item || item.sha256 !== repoReviewedSha || $('repo-source-panel').hidden) return;
    const parsed = normalizePastedUnifiedDiff($('repo-import-source').value);
    if (parsed.error) {
      $('repo-import-message').textContent = parsed.error;
      return;
    }
    if (parsed.relativePath !== item.relative_path) {
      $('repo-import-message').textContent = `El diff corresponde a ${safeText(parsed.relativePath, 120)}, pero has leído ${safeText(item.relative_path, 120)}. Selecciona y lee el archivo indicado antes de importarlo.`;
      return;
    }
    const unifiedDiff = parsed.unifiedDiff;
    const regression = $('repo-regression-source').value.trim();
    if (item.relative_path !== 'scripts/build_scorecard.py' && !regression) {
      $('repo-import-message').textContent = 'Para verificar otro archivo en Docker, escribe primero una prueba pytest que falle con el original y pase con la corrección.';
      return;
    }
    if (regression && (regression.length < 20 || regression.length > 6000 || !/\bdef\s+test_\w+\s*\(/.test(regression))) {
      $('repo-import-message').textContent = 'La regresión debe definir una función pytest test_ y tener entre 20 y 6000 caracteres.';
      return;
    }
    if (!window.confirm(`¿Validar el diff pegado contra ${item.relative_path}? No contacta a Nemotron ni ejecuta Docker. Su origen NO se considerará autenticado.`)) return;
    const epoch = repoReadEpoch;
    $('repo-import-diff').disabled = true;
    $('repo-run-reviewed').disabled = true;
    repoReviewOffer = null;
    $('repo-import-message').textContent = 'Comprobando que el diff coincide exactamente con el archivo autorizado...';
    try {
      const d = await demoRequest('/demo/repo/import-diff', {
        relative_path:item.relative_path, expected_sha256:item.sha256,
        unified_diff:unifiedDiff, confirmation:'IMPORTAR DIFF PARA REVISION SIN NEBIUS',
        ...(regression ? {regression_source:regression} : {})
      });
      if (epoch !== repoReadEpoch || item.relative_path !== $('repo-file-choice').value) return;
      if (d.mode !== 'imported_review_only' || d.source !== 'user_imported_unverified' ||
          d.base_sha256 !== item.sha256 || d.applied !== false || d.executed !== false ||
          d.authenticity_verified !== false || typeof d.docker_execution_enabled !== 'boolean' ||
          !/^[a-f0-9]{64}$/.test(d.patchset_sha256 || '') ||
          d.relative_path !== item.relative_path ||
          (d.docker_execution_enabled && typeof d.review_id !== 'string')) {
        throw new Error('Importación rechazada: no se pudo confirmar el enlace del diff');
      }
      repoReviewOffer = d.docker_execution_enabled ? {
        review_id:d.review_id, expected_sha256:d.base_sha256,
        patchset_sha256:d.patchset_sha256,
        ...(d.regression_sha256 ? {regression_sha256:d.regression_sha256} : {})
      } : null;
      $('repo-proposal-panel').hidden = false;
      $('repo-proposal-message').textContent = 'Diff importado y revisado. Origen NO autenticado; sin aplicar cambios.';
      $('repo-proposal-meta').textContent = `Parche vinculado: ${safeText(d.patchset_sha256, 75)} · Sin llamada a Nebius.`;
      $('repo-proposal-diff').textContent = safeText(d.file.unified_diff,16000);
      $('repo-run-reviewed').disabled = !d.docker_execution_enabled;
      $('repo-run-message').textContent = d.docker_execution_enabled ?
        (d.regression_sha256 ? 'Prueba pytest vinculada al diff y lista para Docker; Trust solicitará tu autorización.' : 'Regresión de scorecard lista para Docker; Trust solicitará tu autorización.') :
        'Diff revisado sin ejecución. Añade una prueba pytest específica para habilitar Docker.';
      $('repo-import-message').textContent = 'Diff validado sin inferencia. Revisa su contenido antes de ejecutar.';
    } catch(err) {
      $('repo-import-message').textContent = `Importación no aceptada: ${safeText(err.message,180)}`;
    } finally {
      if (epoch === repoReadEpoch) $('repo-import-diff').disabled = false;
    }
  }

  async function runReviewedRepoRepair() {
    const offer = repoReviewOffer;
    const item = repoInventory.find(x => x.relative_path === $('repo-file-choice').value);
    if (!offer || !item ||
        item.sha256 !== offer.expected_sha256 || repoReviewedSha !== offer.expected_sha256 ||
        $('repo-proposal-panel').hidden || !demoEnabled) return;
    if (!window.confirm(`¿Verificar el diff revisado en dos copias TEMPORALES de ${item.relative_path} con Docker y Trust? La prueba pytest también puede contener código. NO se modificará tu repositorio y NO se llamará a Nebius. El candidato puede fallar las pruebas.`)) return;
    $('repo-run-reviewed').disabled = true;
    $('repo-run-message').textContent = 'Solicitando ejecución del diff revisado (se requieren autorizaciones individuales)...';
    $('demo-result').hidden = true;
    demoLastResult = '';
    try {
      const data = await demoRequest('/demo/repo/execute', {
        ...offer, confirmation:'EJECUTAR COPIA REVISADA EN DOCKER'
      });
      repoReviewOffer = null; // one-use review
      $('repo-run-message').textContent = 'Revisa las autorizaciones pendientes en el panel Docker, justo debajo.';
      showDemo(data);
      pollDemo();
      $('verified-demo').scrollIntoView({behavior:'smooth',block:'start'});
    } catch (err) {
      $('repo-run-message').textContent = `No se inició la ejecución: ${safeText(err.message, 150)}. Revisa nuevamente el diff si caducó.`;
      $('repo-run-reviewed').disabled = false;
    }
  }

  function updateRepoFileMetadata() {
    clearRepoPreview();
    const selection = $('repo-file-choice').value;
    const file = repoInventory.find(x => x.relative_path === selection);
    $('repo-file-metadata').textContent = file ?
      `Ruta: ${safeText(file.relative_path, 180)} · ${file.size_bytes} bytes · SHA-256: ${file.sha256 || 'no calculado'}` : '';
  }

  async function inspectRepository() {
    if (!repoInspectionAvailable) return;
    clearRepoPreview();
    $('repo-inspect').disabled = true;
    $('repo-inspect-message').textContent = 'Leyendo inventario permitido (sin ejecutar código)...';
    try {
      const data = await demoRequest('/demo/repo/inspect');
      if (data.mode !== 'read_only' || !Array.isArray(data.files)) throw new Error('Respuesta de inventario inválida');
      repoInventory = data.files.filter(x => x && x.is_text_candidate === true &&
        typeof x.relative_path === 'string' && x.relative_path.length <= 260);
      const choices = $('repo-file-choice');
      choices.replaceChildren();
      for (const item of repoInventory) {
        const opt = document.createElement('option');
        opt.value = item.relative_path;
        opt.textContent = item.relative_path;
        choices.appendChild(opt);
      }
      $('repo-file-selection').hidden = repoInventory.length === 0;
      const warnings = data.truncated ? ' Inventario parcial: se alcanzó un límite de exploración.' : '';
      $('repo-inspect-message').textContent =
        `Repositorio: ${safeText(data.repository, 85)} · ${data.file_count} archivos encontrados · ${repoInventory.length} textos seleccionables.${warnings} Solo lectura; no se han ejecutado modelos.`;
      updateRepoFileMetadata();
    } catch (error) {
      $('repo-file-selection').hidden = true;
      $('repo-inspect-message').textContent = safeText(error.message, 180);
    } finally { $('repo-inspect').disabled = false; }
  }

  function useRepoFile() {
    const selected = $('repo-file-choice').value;
    if (!repoInventory.some(item => item.relative_path === selected)) return;
    const current = $('repo-goal').value.trim();
    const appendix = `\nArchivo priorizado para revisión: ${selected}`;
    if (current.length + appendix.length > 900) {
      $('repo-inspect-message').textContent = 'Reduce el objetivo antes de vincular este archivo.';
      return;
    }
    if (!current.includes(appendix)) $('repo-goal').value = current + appendix;
    $('repo-inspect-message').textContent = 'Archivo vinculado al objetivo de ingeniería. No se ha leído su contenido ni ejecutado ninguna herramienta.';
  }

  function setupWorkspaces() {
    for(const button of document.querySelectorAll('[data-mode]')) button.addEventListener('click',()=>selectWorkspace(button.dataset.mode));
    $('nexus-to-task').addEventListener('click',makeNexusTask);
    $('grid-to-task').addEventListener('click',makeGridTask);
    $('grid-simulate').addEventListener('click',simulateGrid);
    $('grid-multiday').addEventListener('click',simulateGridMultiday);
    $('grid-multiday-export').addEventListener('click',exportGridMultiday);
    $('grid-export-evidence').addEventListener('click',exportGridEvidence);
    $('live-add').addEventListener('click',addLiveProposal);
    $('live-idea').addEventListener('keydown',(event)=>{if(event.key==='Enter'){event.preventDefault();addLiveProposal();}});
    $('live-to-task').addEventListener('click',makeLiveTask);
    $('live-reset').addEventListener('click',()=>{state.proposals=[];renderLiveProposals();$('workspace-message').textContent='Simulación de votación reiniciada.';});
    renderLiveProposals();selectWorkspace('nexus');
  }

  async function initialize() {
    window.addEventListener('hashchange',navigation);navigation();
    $('select-files').addEventListener('click',()=>$('file-picker').click());
    $('file-picker').addEventListener('change',(event)=>{loadFiles(event.target.files);event.target.value='';});
    const zone=$('drop-zone');zone.addEventListener('click',()=>$('file-picker').click());
    zone.addEventListener('keydown',(event)=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();$('file-picker').click();}});
    zone.addEventListener('dragover',(event)=>{event.preventDefault();zone.classList.add('dragging');});
    zone.addEventListener('dragleave',()=>zone.classList.remove('dragging'));
    zone.addEventListener('drop',(event)=>{event.preventDefault();zone.classList.remove('dragging');loadFiles(event.dataTransfer?.files);});
    $('connect-btn').addEventListener('click',connect);
    $('disconnect-btn').addEventListener('click',disconnect);
    $('prepare-btn').addEventListener('click',prepare);
    $('preview-btn').addEventListener('click',preview);
    $('finalize-btn').addEventListener('click',finalize);
    setupWorkspaces();
    $('repo-inspect').addEventListener('click', inspectRepository);
    $('repo-file-choice').addEventListener('change', updateRepoFileMetadata);
    $('repo-use-file').addEventListener('click', useRepoFile);
    $('repo-read-file').addEventListener('click', readRepoFile);
    $('repo-propose').addEventListener('click', previewRepoProposal);
    $('repo-nebius-check-model').addEventListener('click', checkNemotronCatalog);
    $('repo-nebius-propose').addEventListener('click', proposeWithNemotron);
    $('repo-revision-feedback').addEventListener('click', prepareRevisionFeedback);
    $('repo-import-diff').addEventListener('click', importPreviouslyReviewedDiff);
    $('repo-run-reviewed').addEventListener('click', runReviewedRepoRepair);
    $('demo-start').addEventListener('click', startDemo);
    $('demo-refresh').addEventListener('click', refreshDemo);
    $('demo-approve').addEventListener('click', () => decideDemo('approve'));
    $('demo-deny').addEventListener('click', () => decideDemo('deny'));
    $('clear-evidence').addEventListener('click',resetEvidence);
    try { const r = await fetch('/web-status',{cache:'no-store'});if(!r.ok)throw new Error();const d=await r.json();
      state.mode=d.mode==='live'?'live':'offline';
      gridCheckpointEnabled=d.aegisgrid_mission_checkpoint_enabled===true;
      demoEnabled = d.synthetic_demo_available === true;
      repoInspectionAvailable = d.repo_inspection_available === true;
      nebiusProposalEnabled = d.nebius_code_proposals_enabled === true;
      $('repo-nebius-check-model').disabled = !nebiusProposalEnabled;
      $('repo-nebius-message').textContent = nebiusProposalEnabled ?
        'Disponible con consentimiento. Una solicitud inicial y, tras una reparación fallida en Docker, una revisión adicional explícita.' :
        'Nebius desactivado. Usa --allow-nebius-proposals y NEBIUS_API_KEY solo si autorizas posibles costos.';
      $('repo-inspect').disabled = !repoInspectionAvailable;
      $('repo-inspect-message').textContent = repoInspectionAvailable ?
        'Inspección disponible. Pega el token local en Verified Repair y presiona Inspeccionar repositorio.' :
        'Inspección deshabilitada. Inicia el servidor con --verified-demo y --inspect-repo.';
      $('demo-start').disabled = !demoEnabled;
      $('demo-refresh').disabled = !demoEnabled;
      $('demo-image').textContent = demoEnabled ?
        `Imagen fijada: ${safeText(d.synthetic_demo_image, 130)}. Solo calculadora sintética; Nebius desactivado.` :
        'Ejecución no habilitada. Inicia el servidor con --verified-demo y una imagen Docker fijada.';
      $('demo-message').textContent = demoEnabled ? 'Ingresa el token local y consulta el estado; las aprobaciones se muestran aquí.' : 'Modo visual de planificación solamente.';
      $('sidebar-mode').textContent=demoEnabled?'Demo Docker local supervisada':(state.mode==='live'?'Planificador LIVE disponible':'Modo de archivos locales');
      $('server-pill').textContent=demoEnabled?'DEMO DOCKER LOCAL':(state.mode==='live'?'API LOCAL ACTIVA':'MODO OFFLINE');
      $('mode-dot').style.background=state.mode==='live'?'#65dfaa':'#f1c46e';
      $('api-badge').textContent=state.mode==='live'?'Token requerido':'Servidor sin modo live';
      if(state.mode==='offline')setMsg(demoEnabled?'Demo Docker sintética disponible. Para Nebius se utiliza un modo separado.':'Modo offline: puedes explorar tres espacios e importar evidencia; inicia --live para planificar con Nemotron.');
    }catch(_err){$('server-pill').textContent='SIN SERVIDOR';$('sidebar-mode').textContent='Abre mediante el servidor local';setMsg('No se detecta el servidor local de Nexus. Inicia su servidor web (normalmente 127.0.0.1:8012).');}
    updateControls();
  }
  document.addEventListener('DOMContentLoaded',initialize);
})();
