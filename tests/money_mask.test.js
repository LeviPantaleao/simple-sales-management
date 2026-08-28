// Testa o algoritmo da máscara de dinheiro estilo caixa eletrônico usada em
// viewables/sale.html (funções moneyMaskCents/renderMoneyMask/enforceMoneyMask).
// Roda com: node --test tests\money_mask.test.js
//
// Este arquivo replica a lógica pura (sem DOM) para poder testá-la fora do
// Electron/navegador. Se o algoritmo em sale.html mudar, atualize aqui junto.

const test = require('node:test');
const assert = require('node:assert/strict');

const MONEY_MASK_MAX_CENTS = 999999999999;

function pushDigits(cents, digitsStr) {
  let cur = cents;
  for (const ch of digitsStr) cur = Math.min(MONEY_MASK_MAX_CENTS, cur * 10 + Number(ch));
  return cur;
}

function popDigit(cents) {
  return Math.trunc(cents / 10);
}

function formatCents(cents) {
  return (cents / 100).toFixed(2);
}

test('dígitos entram da casa menor (centavos) para a maior', () => {
  let cents = 0;
  cents = pushDigits(cents, '2'); assert.equal(formatCents(cents), '0.02');
  cents = pushDigits(cents, '0'); assert.equal(formatCents(cents), '0.20');
  cents = pushDigits(cents, '0'); assert.equal(formatCents(cents), '2.00');
  cents = pushDigits(cents, '9'); assert.equal(formatCents(cents), '20.09');
  cents = pushDigits(cents, '1'); assert.equal(formatCents(cents), '200.91');
});

test('backspace remove o dígito mais à direita (o menos significativo)', () => {
  let cents = pushDigits(0, '20091');
  assert.equal(formatCents(cents), '200.91');
  cents = popDigit(cents);
  assert.equal(formatCents(cents), '20.09');
});

test('backspace repetido zera o valor', () => {
  let cents = pushDigits(0, '20091');
  for (let i = 0; i < 10; i++) cents = popDigit(cents);
  assert.equal(cents, 0);
  assert.equal(formatCents(cents), '0.00');
});

test('colar múltiplos dígitos de uma vez empurra todos em sequência', () => {
  const cents = pushDigits(0, '1234567');
  assert.equal(formatCents(cents), '12345.67');
});

test('respeita o teto máximo em vez de estourar/virar negativo', () => {
  let cents = pushDigits(0, '9999999999999999');
  assert.equal(cents, MONEY_MASK_MAX_CENTS);
  assert.ok(cents > 0);
});

test('caracteres não numéricos são ignorados antes de entrar na máscara (equivalente ao replace(/[^\\d]/g, \'\') em sale.html)', () => {
  const raw = '2,0a0-9 1';
  const digits = raw.replace(/[^\d]/g, '');
  assert.equal(digits, '20091');
  assert.equal(formatCents(pushDigits(0, digits)), '200.91');
});
