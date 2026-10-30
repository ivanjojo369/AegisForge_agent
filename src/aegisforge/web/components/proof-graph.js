/** Accessible, dependency-free proof relationships; integrity != authenticity. */
export function renderProofGraph(root, graph = {}) {
  if (!root || typeof root.replaceChildren !== 'function') throw new TypeError('invalid graph view');
  const doc = root.ownerDocument;
  const section = doc.createElement('section'); section.setAttribute('aria-label', 'ProofGraph');
  const heading = doc.createElement('h3'); heading.textContent = 'ProofGraph'; section.append(heading);
  const nodes = Array.isArray(graph.nodes) ? graph.nodes.slice(0, 200) : [];
  const edges = Array.isArray(graph.edges) ? graph.edges.slice(0, 300) : [];
  const summary = doc.createElement('p'); summary.textContent = `${nodes.length} nodos · ${edges.length} relaciones`; section.append(summary);
  const list = doc.createElement('ul');
  for (const n of nodes) { const li = doc.createElement('li'); li.textContent = `${String(n.id ?? '?')}: ${String(n.kind ?? 'unknown')}`; list.append(li); }
  section.append(list);
  const relationList = doc.createElement('ul');
  for (const edge of edges) { const li = doc.createElement('li'); li.textContent = `${String(edge.source ?? '?')} → ${String(edge.target ?? '?')} (${String(edge.relation ?? 'relation')})`; relationList.append(li); }
  section.append(relationList);
  const note = doc.createElement('small'); note.textContent = 'Relaciones declaradas; no certifican autenticidad.'; section.append(note);
  root.replaceChildren(section);
}
