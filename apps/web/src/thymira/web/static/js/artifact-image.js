/** Build a raster preview only from the scoped API's verified binary projection. */
export async function artifactImageSource(result, runId, artifactId) {
  if (result?.run_id !== runId || result.artifact?.id !== artifactId ||
      !result.artifact.valid || result.encoding !== "base64" || result.redacted ||
      typeof result.content !== "string" || !result.content || result.content.length > 2800000) return null;
  let bytes;
  try { bytes = Uint8Array.from(atob(result.content), (char) => char.charCodeAt(0)); }
  catch { return null; }
  if (bytes.length !== result.artifact.size_bytes) return null;
  const starts = (...signature) => signature.every((value, index) => bytes[index] === value);
  const mime = starts(137, 80, 78, 71, 13, 10, 26, 10) ? "image/png"
    : starts(255, 216, 255) ? "image/jpeg"
      : starts(71, 73, 70, 56) ? "image/gif"
        : starts(82, 73, 70, 70) && bytes[8] === 87 && bytes[9] === 69 && bytes[10] === 66 && bytes[11] === 80 ? "image/webp" : null;
  if (!mime || !/^[a-f0-9]{64}$/.test(result.artifact.sha256 ?? "")) return null;
  if (globalThis.crypto?.subtle) {
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    const hex = [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("");
    if (hex !== result.artifact.sha256) return null;
  }
  return `data:${mime};base64,${result.content}`;
}
