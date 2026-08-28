// Testa o algoritmo da máscara de dinheiro estilo caixa eletrônico usada em
// viewables/management.html. As funções puras (pushDigits/popDigit/
// sanitizeDigits/clampCents) vivem em viewables/money-mask.js e são as MESMAS
// que o app carrega no navegador/Electron — aqui elas são importadas, não
// recopiadas, então o teste quebra de verdade se o algoritmo mudar.
//
// Roda com: node --test tests\money_mask.test.js

const test = require('node:test');
const assert = require('node:assert/strict');

const {
  MONEY_MASK_MAX_CENTS,
  clampCents,
  sanitizeDigits,
  pushDigits,
  popDigit,
} = require('../viewables/money-mask.js');

// Helper só do teste: centavos inteiros -> "R$ cru" com 2 casas.
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

test('caracteres não numéricos são ignorados antes de entrar na máscara', () => {
  const raw = '2,0a0-9 1';
  const digits = sanitizeDigits(raw);
  assert.equal(digits, '20091');
  assert.equal(formatCents(pushDigits(0, digits)), '200.91');
  // pushDigits também sanitiza internamente, então o texto cru chega ao mesmo lugar.
  assert.equal(pushDigits(0, raw), pushDigits(0, digits));
});

test('sanitizeDigits lida com null/undefined/vazio sem quebrar', () => {
  assert.equal(sanitizeDigits(null), '');
  assert.equal(sanitizeDigits(undefined), '');
  assert.equal(sanitizeDigits(''), '');
  assert.equal(sanitizeDigits('R$ 1.234,56'), '123456');
});

test('clampCents arredonda e prende no intervalo [0, MAX]', () => {
  assert.equal(clampCents(-5), 0);
  assert.equal(clampCents(10.4), 10);
  assert.equal(clampCents(10.5), 11);
  assert.equal(clampCents(NaN), 0);
  assert.equal(clampCents(MONEY_MASK_MAX_CENTS + 1000), MONEY_MASK_MAX_CENTS);
});
