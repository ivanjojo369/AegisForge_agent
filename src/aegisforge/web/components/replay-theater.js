/** Replay recorded events as a timeline, never re-execute old effects. */
export function renderReplayTheater(root, events = [], onSelect = undefined) {
  if (!root || typeof root.replaceChildren !== 'function' || !Array.isArray(events) ||
      (onSelect !== undefined && typeof onSelect !== 'function')) throw new TypeError('invalid replay view');
  const doc = root.ownerDocument;
  const section = doc.createElement('section'); section.setAttribute('aria-label', 'Replay Theater');
  const heading = doc.createElement('h3'); heading.textContent = 'Replay Theater · solo lectura'; section.append(heading);
  const list = doc.createElement('ol');
  const ordered = events.slice(0, 300).sort((a,b) => (Number(a.sequence) || 0) - (Number(b.sequence) || 0));
  for (const event of ordered) {
    const li = doc.createElement('li');
    if (onSelect) {
      const button = doc.createElement('button'); button.type = 'button';
      button.textContent = `${String(event.operation ?? 'Evento')} · ${String(event.at_utc ?? '')}`;
      button.addEventListener('click', () => onSelect(event)); li.append(button);
    } else { li.textContent = `${String(event.operation ?? 'Evento')} · ${String(event.at_utc ?? '')}`; }
    list.append(li);
  }
  section.append(list); root.replaceChildren(section);
}
