import { describe, it, expect } from 'vitest';
import { parseBodyCondition } from './align';

describe('parseBodyCondition', () => {
  it('parses column/op/number', () => {
    expect(parseBodyCondition('bmi<22')).toEqual({ column: 'bmi', op: '<', value: 22 });
    expect(parseBodyCondition('body_fat>=20.5')).toEqual({ column: 'body_fat', op: '>=', value: 20.5 });
  });
  it('rejects unknown columns and bad syntax', () => {
    expect(() => parseBodyCondition('course<3')).toThrow(/unknown/);
    expect(() => parseBodyCondition('bmi!!22')).toThrow(/invalid/);
  });
});
