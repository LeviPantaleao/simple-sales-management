/* money-mask.js — pure "ATM-style" currency helpers.
 *
 * Single source of truth for the digit arithmetic behind the money input.
 * Loaded in the browser/Electron renderer *before* app-common.js (which wires
 * these to real DOM elements), and required directly by the Node test runner.
 *
 * "ATM-style": each typed digit enters at the cents place and pushes the
 * previous digits one decimal place to the left, e.g. typing 2,0,0,9,1 shows
 * 0.02 -> 0.20 -> 2.00 -> 20.09 -> 200.91. The real value is an integer number
 * of cents; turning that into a localized string is done elsewhere
 * (toMoneyRaw in app-common.js).
 *
 * These functions take and return plain numbers/strings — no DOM, no events,
 * no side effects — so they can be unit-tested on their own.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;            // Node / test runner
  }
  if (root) {
    root.MoneyMask = api;            // browser / Electron renderer
  }
})(
  typeof globalThis !== 'undefined'
    ? globalThis
    : (typeof window !== 'undefined' ? window : this),
  function () {
    'use strict';

    // Generous ceiling: 9,999,999,999.99 expressed in cents.
    var MONEY_MASK_MAX_CENTS = 999999999999;

    // Clamp to [0, MONEY_MASK_MAX_CENTS] and coerce to a whole number of cents.
    function clampCents(cents) {
      return Math.max(0, Math.min(MONEY_MASK_MAX_CENTS, Math.round(cents) || 0));
    }

    // Keep only ASCII digits (0-9); everything else is dropped.
    function sanitizeDigits(value) {
      return String(value == null ? '' : value).replace(/[^\d]/g, '');
    }

    // Append one or more digits at the cents place, each digit shifting the
    // running value left by one decimal. Clamps at every step, so even a long
    // paste can never overflow or wrap negative.
    function pushDigits(cents, digitsStr) {
      var cur = clampCents(cents);
      var digits = sanitizeDigits(digitsStr);
      for (var i = 0; i < digits.length; i++) {
        cur = Math.min(MONEY_MASK_MAX_CENTS, cur * 10 + Number(digits[i]));
      }
      return cur;
    }

    // Drop the least-significant digit (backspace / delete).
    function popDigit(cents) {
      return Math.trunc(clampCents(cents) / 10);
    }

    return {
      MONEY_MASK_MAX_CENTS: MONEY_MASK_MAX_CENTS,
      clampCents: clampCents,
      sanitizeDigits: sanitizeDigits,
      pushDigits: pushDigits,
      popDigit: popDigit
    };
  }
);
