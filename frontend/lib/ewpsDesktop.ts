type TauriWindow = Window & { __TAURI_INTERNALS__?: unknown };

export function isTauriDesktop(): boolean {
  return typeof window !== "undefined" && Boolean((window as TauriWindow).__TAURI_INTERNALS__);
}

export async function openEWPSExportFolder(): Promise<string> {
  if (!isTauriDesktop()) {
    throw new Error("Open export folder is available in the Windows desktop application only.");
  }
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<string>("open_ewps_export_folder");
}
