/** Read-only view of host-supplied council statements. No approval action. */
export function renderAgentCouncil(root, statements = []) {
  if (!root || typeof root.replaceChildren !== 'function' || !Array.isArray(statements)) throw new TypeError('invalid council view');
  const doc = root.ownerDocument;
  const section = doc.createElement('section');
  section.setAttribute('aria-label', 'Agent Council');
  const h = doc.createElement('h3'); h.textContent = 'Agent Council'; section.append(h);
  if (!statements.length) {
    const p = doc.createElement('p'); p.textContent = 'Sin deliberaciones registradas.'; section.append(p);
  }
  for (const row of statements.slice(0, 60)) {
    const card = doc.createElement('article');
    const title = doc.createElement('strong'); title.textContent = String(row.role ?? 'Agente');
    const status = doc.createElement('p'); status.textContent = String(row.verdict ?? 'Sin veredicto');
    const detail = doc.createElement('p'); detail.textContent = String(row.rationale ?? '');
    card.append(title, status, detail); section.append(card);
  }
  root.replaceChildren(section);
}
