/**
 * Browser implementation of ``SlashCommandContext.saveTextFile``: the
 * web host can't write to the user's filesystem directly, so "saving"
 * means triggering a download of the file's basename. Returns the same
 * ``{saved:true, absPath, bytes}`` shape the TUI's Node writer returns —
 * ``absPath`` echoes the requested path (the browser picks the real
 * destination), ``bytes`` is the UTF-8 length the ok-log reports.
 * Browsers can't probe ``alreadyExists`` (no FS access), so that branch
 * never fires here.
 */
export async function saveTextFile(
  outPath: string,
  content: string,
): Promise<{ saved: true; absPath: string; bytes: number }> {
  const bytes = new TextEncoder().encode(content).length;
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = outPath.split("/").pop() ?? outPath;
  a.click();
  // Revoke on the next task, not synchronously: Safari can abort the
  // download when the object URL dies in the same task as click().
  setTimeout(() => URL.revokeObjectURL(url), 0);
  return { saved: true, absPath: outPath, bytes };
}
