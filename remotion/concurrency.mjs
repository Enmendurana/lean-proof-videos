export const parseConcurrency = (rawValue) => {
  const value = String(rawValue).trim();
  if (/^[1-9]\d*$/.test(value)) {
    return Number.parseInt(value, 10);
  }
  const percentage = /^(\d{1,3})%$/.exec(value);
  if (percentage) {
    const amount = Number.parseInt(percentage[1], 10);
    if (amount >= 1 && amount <= 100) return `${amount}%`;
  }
  throw new Error(
    `Invalid --concurrency value ${JSON.stringify(rawValue)}; use a positive integer or 1%-100%.`,
  );
};
