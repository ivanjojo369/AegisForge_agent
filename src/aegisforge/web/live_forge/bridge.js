'use strict';
// Read-only virtual activities. Text-only rendering: no execution or HTML from events.
let last = '';
const EXTRA_ACTIVITIES = ['gaming','music','sports','talk'];
function ensureExtraCards(contracts){
  const box = byId('worlds');
  for(const name of EXTRA_ACTIVITIES){
    if (byId(name)) continue;
    const contract = contracts.find(c=>c.activity===name);
    if(!contract) throw new Error('missing_activity_contract');
    const card=document.createElement('article');card.className='world';card.id=name;
    const h=document.createElement('h2');h.textContent=contract.label+' · actividad virtual';card.appendChild(h);
    const metrics=document.createElement('div');metrics.className='metrics';
    for(const [key,label] of Object.entries(contract.metric_labels)){
      const metric=document.createElement('div');metric.className='metric';metric.append(document.createTextNode(label));
      const value=document.createElement('b');value.className=key;value.textContent='0 / 24';metric.appendChild(value);metrics.appendChild(metric);
    }
    card.appendChild(metrics);
    const details=document.createElement('p');details.className='details';details.textContent='Sin modificaciones';card.appendChild(details);
    const section=document.createElement('section');section.className='detail-group';
    const title=document.createElement('h3');title.textContent='Contenido conceptual creado';section.appendChild(title);
    const hint=document.createElement('p');hint.className='hint';hint.textContent='Solo representaciones virtuales, sin operaciones externas.';section.appendChild(hint);
    const records=document.createElement('ul');records.className='records';section.appendChild(records);
    const historyTitle=document.createElement('h3');historyTitle.textContent='Últimas acciones autorizadas';section.appendChild(historyTitle);
    const history=document.createElement('ol');history.className='history';section.appendChild(history);
    card.appendChild(section);box.appendChild(card);
  }
}
const byId = id => document.getElementById(id);
function renderList(parent, entries, limit) {
  parent.replaceChildren();
  const selected = entries.slice(-limit);
  if (selected.length === 0) {
    const li = document.createElement('li');
    li.textContent = 'Sin registros en esta sesión.';
    parent.appendChild(li);
    return;
  }
  for (const entry of selected) {
    const li = document.createElement('li');
    const title = document.createElement('strong');
    title.textContent = entry.title;
    const body = document.createElement('span');
    body.textContent = ' — ' + entry.description;
    li.append(title, body);
    parent.appendChild(li);
  }
}
function renderVirtualDetails(functional) {
  if (!functional || functional.science?.schema !== 'aegisforge.live_forge.virtual_activity_adapter.v1' ||
      functional.coding?.schema !== 'aegisforge.live_forge.virtual_activity_adapter.v1')
    throw new Error('invalid_functional_adapter');
  const sci = functional.science;
  const coding = functional.coding;
  byId('science-formula').textContent = 'Índice de demostración: ' + sci.model;
  byId('science-summary').textContent = sci.experiments.length + ' comparaciones · ' +
    sci.documented_count + ' documentadas · modo ' + sci.mode +
    (sci.unassigned_milestones ? ' · ' + sci.unassigned_milestones + ' hitos sin experimento' : '');
  const sciList = byId('science-comparisons');
  sciList.replaceChildren();
  for (const item of sci.experiments.slice(-8)) {
    const li = document.createElement('li');
    li.textContent = '#' + item.number + ' ' + item.title + ' · referencia ' +
      item.baseline.index + ' / alternativa ' + item.alternative.index +
      ' · elegido ' + item.selected_index + ' · ' + item.stage;
    sciList.appendChild(li);
  }
  if (!sci.experiments.length) {
    const li=document.createElement('li'); li.textContent='Sin modelos hipotéticos creados.';
    sciList.appendChild(li);
  }
  byId('coding-summary').textContent = coding.tasks.length + ' tareas · ' +
    coding.reviewed_count + ' revisadas conceptualmente · ' + coding.pending_count +
    ' pendientes' + (coding.unassigned_milestones ?
      ' · ' + coding.unassigned_milestones + ' hitos sin tarea' : '');
  const codingList = byId('coding-tasks');
  codingList.replaceChildren();
  for (const item of coding.tasks.slice(-10)) {
    const li=document.createElement('li');
    li.textContent = '#' + item.number + ' ' + item.title + ': ' + item.stage +
      ' · ' + item.role;
    codingList.appendChild(li);
  }
  if (!coding.tasks.length) {
    const li=document.createElement('li');li.textContent='Sin tareas creadas.';
    codingList.appendChild(li);
  }
}
function renderHistory(parent, entries) {
  parent.replaceChildren();
  if (!entries.length) {
    const li = document.createElement('li'); li.textContent = 'Aún no hay acciones registradas.';
    parent.appendChild(li); return;
  }
  for (const item of entries) {
    const li = document.createElement('li');
    li.textContent = 'Revisión ' + item.revision + ': ' + item.title;
    parent.appendChild(li);
  }
}
async function refresh() {
  try {
    const response = await fetch('/api/bridge', {cache:'no-store', credentials:'omit'});
    if (!response.ok) throw new Error('unavailable');
    const data = await response.json();
    const signature = JSON.stringify([data.worlds, data.details, data.functional, data.contracts]);
    ensureExtraCards(data.contracts||[]);
    byId('status').textContent = 'Conectado · actividades virtuales independientes · solo lectura';
    if (signature === last) return;
    for (const name of ['science', 'coding', ...EXTRA_ACTIVITIES]) {
      const world = data.worlds?.[name], detail = data.details?.[name], box = byId(name);
      if (!world || world.schema !== 'aegisforge.live_forge.activity_bridge.v1' ||
          !detail || !Array.isArray(detail.cards) || !Array.isArray(detail.history))
        throw new Error('invalid_world');
      box.dataset.theme = ['teal','violet','amber','blue'].includes(world.theme) ? world.theme : 'teal';
      for (const key of ['tasks','milestones','insights'])
        box.querySelector('.' + key).textContent = world[key] + ' / 24';
      box.querySelector('.details').textContent = 'Revisión ' + world.revision +
        ' · Modo ' + world.mode + ' · Tema ' + world.theme +
        (world.challenge ? ' · Reto activo' : '') +
        (world.roles ? ' · Roles activos' : '') +
        (world.constraint ? ' · Restricción activa' : '');
      renderList(box.querySelector('.records'), detail.cards.slice(-10), 10);
      renderHistory(box.querySelector('.history'), detail.history);
    }
    renderVirtualDetails(data.functional);
    last = signature;
  } catch (_) {
    byId('status').textContent = 'No se pudo cargar el estado detallado de Activity Bridge';
  }
}
refresh(); setInterval(refresh, 1500);
