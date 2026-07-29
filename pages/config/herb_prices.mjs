export const HERB_GRADES = Object.freeze([
  '九品药材',
  '八品药材',
  '七品药材',
  '六品药材',
  '五品药材',
  '四品药材',
  '三品药材',
  '二品药材',
  '一品药材',
]);

function emptyGroups() {
  return Object.fromEntries(HERB_GRADES.map(grade => [grade, {}]));
}

function normalizedName(value) {
  return String(value || '')
    .trim()
    .replace(/[\u200b-\u200f\ufeff\u2060-\u206f]/g, '')
    .replace(/\s+/g, '')
    .replaceAll('：', ':')
    .replaceAll('，', ',');
}

export function normalizeHerbPayload(payload = {}) {
  const groups = emptyGroups();
  for (const grade of HERB_GRADES) {
    const source = payload.groups && typeof payload.groups[grade] === 'object'
      ? payload.groups[grade]
      : {};
    groups[grade] = { ...source };
  }
  const unclassified = payload.unclassified && typeof payload.unclassified === 'object'
    ? { ...payload.unclassified }
    : {};
  return { groups, unclassified };
}

export function firstNonEmptyGrade(groups) {
  return HERB_GRADES.find(grade => Object.keys(groups[grade] || {}).length > 0) || HERB_GRADES[0];
}

export function validateHerbRows(rows) {
  const groups = emptyGroups();
  const errors = [];
  const seen = new Map();
  rows.forEach((row, index) => {
    const grade = String(row.grade || '');
    const name = normalizedName(row.name);
    const price = Number(row.price);
    let code = '';
    if (!HERB_GRADES.includes(grade)) code = 'invalid-grade';
    else if (!name) code = 'empty-name';
    else if (!Number.isFinite(price) || price <= 0) code = 'invalid-price';
    else if (seen.has(name)) code = 'duplicate-name';
    if (code) {
      errors.push({ index, code });
      return;
    }
    seen.set(name, index);
    groups[grade][name] = price;
  });
  return { ok: errors.length === 0, groups, errors };
}
