/** Displays an authorized proposed diff. Never applies changes to disk. */
export function renderCodeStudio(root, proposal = {}) {
  if (!root || typeof root.replaceChildren !== 'function') throw new TypeError('invalid studio view');
  const doc = root.ownerDocument;
  const section = doc.createElement('section'); section.setAttribute('aria-label', 'Code Studio');
  const heading = doc.createElement('h3'); heading.textContent = 'Code Studio · revisión';
  const filename = doc.createElement('p'); filename.textContent = String(proposal.filename ?? 'Sin archivo');
  const pre = doc.createElement('pre'); pre.textContent = String(proposal.diff ?? 'No hay diferencias propuestas.').slice(0, 200_000);
  const note = doc.createElement('p'); note.textContent = 'Vista de solo lectura. Ningún cambio ha sido aplicado.';
  section.append(heading, filename, pre, note); root.replaceChildren(section);
}
