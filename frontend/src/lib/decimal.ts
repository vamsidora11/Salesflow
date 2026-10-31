/**
 * Exact decimal-string addition. Money arrives as strings and must not be summed as
 * floats; Number() is only for display and charting.
 */
function parse(s: string): { neg: boolean; int: string; frac: string } {
  const m = /^\s*([+-])?(\d*)(?:\.(\d*))?\s*$/.exec(s);
  if (!m) throw new Error(`Invalid decimal: ${s}`);
  return { neg: m[1] === "-", int: m[2] || "0", frac: m[3] ?? "" };
}

function toScaled(s: string, scale: number): bigint {
  const p = parse(s);
  const v = BigInt(p.int + p.frac.padEnd(scale, "0"));
  return p.neg ? -v : v;
}

export function addDecimal(a: string, b: string, minScale = 2): string {
  const pa = parse(a);
  const pb = parse(b);
  const scale = Math.max(pa.frac.length, pb.frac.length, minScale);
  const sum = toScaled(a, scale) + toScaled(b, scale);
  const neg = sum < 0n;
  const digits = (neg ? -sum : sum).toString().padStart(scale + 1, "0");
  const int = digits.slice(0, digits.length - scale);
  const frac = digits.slice(digits.length - scale);
  return `${neg ? "-" : ""}${int}${scale > 0 ? "." + frac : ""}`;
}
