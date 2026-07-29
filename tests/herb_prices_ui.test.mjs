import test from 'node:test';
import assert from 'node:assert/strict';

import {
  HERB_GRADES,
  firstNonEmptyGrade,
  normalizeHerbPayload,
  validateHerbRows,
} from '../pages/config/herb_prices.mjs';


test('品级固定按九品至一品排列', () => {
  assert.deepEqual(HERB_GRADES, [
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
});

test('载荷补齐空品级并保留待分类药材', () => {
  const model = normalizeHerbPayload({
    groups: { '七品药材': { '凤血果': 370 } },
    unclassified: { '自定义草': 25 },
  });

  assert.deepEqual(model.groups['九品药材'], {});
  assert.deepEqual(model.groups['七品药材'], { '凤血果': 370 });
  assert.deepEqual(model.groups['一品药材'], {});
  assert.deepEqual(model.unclassified, { '自定义草': 25 });
});

test('默认展开首个非空品级且空数据回退九品', () => {
  const model = normalizeHerbPayload({
    groups: { '七品药材': { '凤血果': 370 } },
  });

  assert.equal(firstNonEmptyGrade(model.groups), '七品药材');
  assert.equal(firstNonEmptyGrade(normalizeHerbPayload().groups), '九品药材');
});

test('保存校验生成嵌套分组', () => {
  const result = validateHerbRows([
    { grade: '九品药材', name: ' 尘磊岩麟果 ', price: '960' },
    { grade: '一品药材', name: '清灵草', price: '12' },
  ]);

  assert.equal(result.ok, true);
  assert.equal(result.groups['九品药材']['尘磊岩麟果'], 960);
  assert.equal(result.groups['一品药材']['清灵草'], 12);
  assert.deepEqual(result.errors, []);
});

test('保存校验拒绝重复名称和非法行', () => {
  const duplicate = validateHerbRows([
    { grade: '九品药材', name: '同 名药', price: '10' },
    { grade: '八品药材', name: '同名药', price: '20' },
  ]);
  const invalid = validateHerbRows([
    { grade: '', name: '待分类药', price: '1' },
    { grade: '九品药材', name: '', price: '1' },
    { grade: '九品药材', name: '零价药', price: '0' },
  ]);

  assert.equal(duplicate.ok, false);
  assert.deepEqual(duplicate.errors, [{ index: 1, code: 'duplicate-name' }]);
  assert.equal(invalid.ok, false);
  assert.deepEqual(invalid.errors, [
    { index: 0, code: 'invalid-grade' },
    { index: 1, code: 'empty-name' },
    { index: 2, code: 'invalid-price' },
  ]);
});
