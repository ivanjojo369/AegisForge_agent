/** View of supplied test observations, never inferred from missing evidence. */
export function renderEvaluationLab(root, observations = []) {
  if (!root || typeof root.replaceChildren !== 'function' || !Array.isArray(observations)) throw new TypeError('invalid evaluation view');
  const doc = root.ownerDocument;
  const section = doc.createElement('section'); section.setAttribute('aria-label', 'Evaluation Lab');
  const heading = doc.createElement('h3'); heading.textContent = 'Evaluation Lab'; section.append(heading);
  const counters = { passed: 0, failed: 0, inconclusive: 0 };
  const list = doc.createElement('ul');
  for (const row of observations.slice(0, 200)) {
    const status = String(row.status ?? 'inconclusive').toLowerCase();
    counters[Object.hasOwn(counters, status) ? status : 'inconclusive']++;
    const li = doc.createElement('li'); li.textContent = `${String(row.name ?? 'Evaluación')}: ${status}`; list.append(li);
  }
  const summary = doc.createElement('p'); summary.textContent = `${counters.passed} aprobadas · ${counters.failed} fallidas · ${counters.inconclusive} inconclusas`;
  section.append(summary, list); root.replaceChildren(section);
}
