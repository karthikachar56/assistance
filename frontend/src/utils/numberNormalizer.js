const INDIC_DIGIT_BLOCKS = ["०", "೦", "௦", "౦", "૦", "০"];

function normalizeIndicDigits(str) {
  let out = "";
  for (const ch of str) {
    const code = ch.charCodeAt(0);
    let mapped = ch;
    for (const zeroChar of INDIC_DIGIT_BLOCKS) {
      const z = zeroChar.charCodeAt(0);
      if (code >= z && code <= z + 9) {
        mapped = String(code - z);
        break;
      }
    }
    out += mapped;
  }
  return out;
}

const WORD_MAP = {
  zero: 0, oh: 0, one: 1, two: 2, three: 3, four: 4, five: 5,
  six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
  eleven: 11, twelve: 12, thirteen: 13, fourteen: 14, fifteen: 15,
  sixteen: 16, seventeen: 17, eighteen: 18, nineteen: 19,
  twenty: 20, thirty: 30, forty: 40, fourty: 40, fifty: 50,
  sixty: 60, seventy: 70, eighty: 80, ninety: 90,
  ek: 1, do: 2, don: 2, teen: 3, chaar: 4, char: 4, paanch: 5, panch: 5, paach: 5,
  chhe: 6, saat: 7, aath: 8, nau: 9, das: 10, bees: 20, vees: 20, tees: 30,
  chaalis: 40, chalis: 40, pachaas: 50, pachas: 50, saath: 60, sattar: 70,
  assi: 80, nabbe: 90,
  innooru: 200, innoora: 200,
  munnooru: 300, muunnooru: 300, munnoora: 300, muunnoora: 300,
  naalnooru: 400, nalknooru: 400, nalknoora: 400, naanooru: 400,
  ainooru: 500, ainoora: 500, nalavattu: 40, aivattu: 50,
};

const SCALES = {
  hundred: 100, sau: 100, so: 100, nooru: 100, vanda: 100, vandalu: 100, shambhar: 100,
  thousand: 1000, hazaar: 1000, hazar: 1000, saavira: 1000, aayiram: 1000, veyyi: 1000,
};

const FRACTIONS = { sava: 0.25, sawa: 0.25, sadhe: 0.5, saade: 0.5, dedh: 1.5, dhai: 2.5, paune: -0.25 };

/**
 * Lightweight client-side preview parser for spoken Indian/English numbers & Indic digits.
 */
export function previewSpokenAmount(input) {
  if (!input || typeof input !== "string") return null;
  const ascii = normalizeIndicDigits(input).replace(/[$₹]/g, " ");
  const kMatch = ascii.toLowerCase().match(/\b(\d+(?:\.\d{1,2})?)\s*k\b/);
  if (kMatch) return Math.round(Number(kMatch[1]) * 1000 * 100) / 100;

  const digitMatch = ascii.replace(/,/g, "").match(/\b\d+(?:\.\d{1,2})?\b/);
  if (digitMatch) return Number(digitMatch[0]);

  const tokens = ascii.toLowerCase().replace(/-/g, " ").match(/[a-z]+/g);
  if (!tokens) return null;

  const ENGLISH_DO_PREV = new Set(["can", "to", "you", "i", "we", "they", "how", "what", "why", "will", "would", "should", "could", "did", "not", "please"]);
  const ENGLISH_DO_NEXT = new Set(["you", "this", "that", "it", "not", "so", "more", "for", "with", "now", "today"]);

  const numTokens = tokens.filter((t, idx) => {
    if (t === "so") return idx > 0 && tokens[idx - 1] in WORD_MAP;
    if (t === "do") {
      const prev = idx > 0 ? tokens[idx - 1] : "";
      const next = idx + 1 < tokens.length ? tokens[idx + 1] : "";
      if (next in SCALES || prev in SCALES || next === "rupaye" || next === "rupees" || next === "rs") {
        return true;
      }
      if (ENGLISH_DO_PREV.has(prev) || ENGLISH_DO_NEXT.has(next)) {
        return false;
      }
      return next in WORD_MAP || prev in WORD_MAP;
    }
    return t in WORD_MAP || t in SCALES || t in FRACTIONS;
  });
  if (numTokens.length === 0) return null;

  if (numTokens[0] in FRACTIONS && numTokens.length >= 2) {
    const mod = FRACTIONS[numTokens[0]];
    if (numTokens.length === 2 && numTokens[1] in SCALES) {
      const scale = SCALES[numTokens[1]];
      if (numTokens[0] === "dedh" || numTokens[0] === "dhai") return mod * scale;
      if (numTokens[0] === "paune") return 0.75 * scale;
      return (1 + mod) * scale;
    }
    if (numTokens.length === 3 && numTokens[1] in WORD_MAP && numTokens[2] in SCALES) {
      return (WORD_MAP[numTokens[1]] + mod) * SCALES[numTokens[2]];
    }
  }

  if (numTokens.length === 2 && numTokens[0] in WORD_MAP && numTokens[1] in WORD_MAP) {
    const a = WORD_MAP[numTokens[0]];
    const b = WORD_MAP[numTokens[1]];
    if (a >= 1 && a <= 9 && b >= 10 && b <= 99) return a * 100 + b;
    if (a >= 20 && a <= 90 && b >= 1 && b <= 9) return a + b;
  }

  let total = 0;
  let current = 0;
  for (const tok of numTokens) {
    if (tok in WORD_MAP) {
      current += WORD_MAP[tok];
    } else if (tok in SCALES) {
      const scale = SCALES[tok];
      if (scale === 100) current = (current || 1) * 100;
      else {
        total += (current || 1) * scale;
        current = 0;
      }
    }
  }
  const sum = total + current;
  return sum > 0 ? sum : null;
}

export function formatINR(value) {
  const num = Number(value || 0);
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: Number.isInteger(num) ? 0 : 2,
  }).format(num);
}
