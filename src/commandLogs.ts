export const logWindowBytes = 256 * 1024;

export function appendLog(previous: string, next: string, reset: boolean) {
  const encoder = new TextEncoder();
  const text = (reset ? '' : previous) + next;
  const bytes = encoder.encode(text);
  if (bytes.length <= logWindowBytes) return text;
  let start = bytes.length - logWindowBytes;
  while ((bytes[start] & 0xc0) === 0x80) start++;
  return new TextDecoder().decode(bytes.subarray(start));
}
